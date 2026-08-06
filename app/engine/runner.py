import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Callable

from app.bazarr.client import BazarrClient
from app.config import Settings
from app.db import repository
from app.engine import poller, prefetch, selector, translator
from app.providers.registry import get_active_provider, get_fallback_provider

logger = logging.getLogger(__name__)


@dataclass
class RunProgress:
    run_id: int | None = None
    triggered_by: str = ""
    total: int = 0
    processed: int = 0
    failed: int = 0
    started_at: float = field(default_factory=time.monotonic)
    active: bool = False
    # The full set of item ids this run WILL touch, captured once at
    # run_batch() start. Needed because an item queued but not yet started
    # has no DB trace linking it to this run_id — item_run_log only gains a
    # row on a terminal outcome, and items.status only reaches 'translating'
    # once its turn in the sequential loop actually arrives. Without this,
    # the Queue page's "current batch" view couldn't show queued-but-not-
    # yet-started items at all (confirmed live: it only showed the single
    # item actively translating, not the other items still waiting).
    item_ids: list[int] = field(default_factory=list)

    @property
    def rate_per_min(self) -> float:
        elapsed_min = (time.monotonic() - self.started_at) / 60
        return round(self.processed / elapsed_min, 1) if elapsed_min > 0 else 0.0

    @property
    def eta_seconds(self) -> float | None:
        if self.processed == 0 or self.rate_per_min == 0:
            return None
        remaining = self.total - self.processed
        return round(remaining / (self.rate_per_min / 60), 1)


class RunController:
    """Orchestration hub: drives a batch (manual-full, scheduled, or
    single-item), tracks live progress for the dashboard's current-run
    panel, and owns provider-fallback selection."""

    def __init__(
        self, conn: sqlite3.Connection, get_client: Callable[[], BazarrClient], settings: Settings
    ):
        self._conn = conn
        # A getter, not a fixed instance — the Bazarr connection settings
        # can be changed and the client rebuilt/closed at any time via the
        # Bazarr Connection page; holding a fixed reference here would keep
        # using a closed client after that happens.
        self._get_client = get_client
        self._settings = settings
        self.current: RunProgress | None = None

    async def run_batch(
        self, items: list[sqlite3.Row], triggered_by: str, enforce_daily_limit: bool = True
    ) -> RunProgress:
        client = self._get_client()
        source_priority = repository.get_config(
            self._conn, "source_lang_priority", default=[]
        )

        ready_items = await selector.resolve_and_gate(
            self._conn, client, items, source_priority
        )

        # A full backlog can be hundreds of hours of LLM work — cap how many
        # items a full-queue/scheduled run will dispatch in a single UTC day
        # rather than grinding through everything in one go. A forced
        # per-item re-run (enforce_daily_limit=False) always bypasses this,
        # since that's an explicit one-off request, not bulk processing.
        daily_limit = self._settings.daily_translation_limit
        if enforce_daily_limit and daily_limit > 0:
            already_done_today = repository.count_completed_today(self._conn)
            remaining = max(0, daily_limit - already_done_today)
            ready_items = ready_items[:remaining]

        run_id = repository.start_run(self._conn, triggered_by)
        progress = RunProgress(
            run_id=run_id,
            triggered_by=triggered_by,
            total=len(ready_items),
            active=True,
            item_ids=[entry["item"]["id"] for entry in ready_items],
        )
        self.current = progress

        # Fetch every item's source subtitle from Bazarr ONCE, up front, in
        # one concurrent burst, instead of once per item spread out over the
        # whole run (which could take hours with pause_between_items_seconds
        # between each one) — keeps the NAS's disks from being woken up
        # repeatedly for the run's entire duration. Any item whose prefetch
        # fails just falls back to a live per-item fetch in translate_item(),
        # so a single bad fetch can't abort the run. Shared flat directory
        # (not per-run_id) so a failed item's cache from an EARLIER run is
        # found and reused here instead of being silently orphaned.
        cached_paths = await prefetch.prefetch_source_subtitles(
            client, ready_items, prefetch.DEFAULT_SCRATCH_ROOT
        )

        active_provider = get_active_provider(self._settings)
        fallback_provider = get_fallback_provider(self._settings)

        pause_seconds = self._settings.pause_between_items_seconds

        # Each engine's batch-size and concurrency settings are tuned for
        # fundamentally different constraints: Ollama's small default
        # protects local VRAM/GPU, while the cloud providers below have no
        # such limit. Sharing one budget between them (as an earlier
        # version of this code did) meant a newly added cloud provider
        # silently inherited Ollama's GPU-safe default and ran far more
        # sequential batches than it needed to — this table is the single
        # place that gap gets closed for every current and future cloud
        # provider, instead of a growing if/elif chain.
        _CLOUD_ENGINE_SETTINGS = {
            "nvidia": (
                self._settings.nvidia_batch_token_budget,
                self._settings.nvidia_concurrent_batch_window,
            ),
            "openrouter": (
                self._settings.openrouter_batch_token_budget,
                self._settings.openrouter_concurrent_batch_window,
            ),
            "groq": (
                self._settings.groq_batch_token_budget,
                self._settings.groq_concurrent_batch_window,
            ),
            "gemini": (
                self._settings.gemini_batch_token_budget,
                self._settings.gemini_concurrent_batch_window,
            ),
        }
        # Local, non-concurrent providers (Ollama, llama.cpp) each still
        # get their OWN batch-token-budget — sharing Ollama's would be
        # fine by coincidence today (same conservative default value) but
        # wrong in spirit, and would silently break if either default
        # ever diverges.
        _LOCAL_ENGINE_BATCH_BUDGETS = {
            "ollama": self._settings.ollama_batch_token_budget,
            "llamacpp": self._settings.llamacpp_batch_token_budget,
        }
        if active_provider.name in _CLOUD_ENGINE_SETTINGS:
            batch_token_budget_override, concurrent_batch_window = _CLOUD_ENGINE_SETTINGS[
                active_provider.name
            ]
        else:
            batch_token_budget_override = _LOCAL_ENGINE_BATCH_BUDGETS.get(
                active_provider.name, self._settings.ollama_batch_token_budget
            )
            concurrent_batch_window = 1  # unused for non-concurrent providers

        try:
            for i, entry in enumerate(ready_items):
                item_id = entry["item"]["id"]
                cached_path = cached_paths.get(item_id)
                try:
                    await translator.translate_item(
                        self._conn,
                        client,
                        entry["item"],
                        entry["source_lang"],
                        entry["source_path"],
                        active_provider,
                        fallback_provider,
                        run_id,
                        num_ctx=self._settings.ollama_num_ctx,
                        batch_token_budget_override=batch_token_budget_override,
                        cached_source_path=cached_path,
                        queue_uploads=self._settings.queue_uploads_enabled,
                        retry_pause_seconds=pause_seconds,
                        concurrent_batch_window=concurrent_batch_window,
                    )
                except Exception:  # noqa: BLE001 - one item's failure must not abort the batch
                    progress.failed += 1
                    logger.exception("Translation failed for item %s", item_id)
                else:
                    # Only clean up the cached source on SUCCESS — a failed
                    # item keeps its cached file so a retry can reuse it
                    # without re-fetching from Bazarr, and so the failure
                    # can be investigated against the exact source that
                    # caused it.
                    prefetch.cleanup_scratch_file(cached_path)
                finally:
                    progress.processed += 1
                # A short rest between items so a long batch doesn't peg the
                # GPU non-stop for hours — skipped after the last item.
                if pause_seconds > 0 and i + 1 < len(ready_items):
                    await asyncio.sleep(pause_seconds)
        finally:
            progress.active = False
            repository.finish_run(self._conn, run_id, progress.processed, progress.failed)
            if hasattr(active_provider, "aclose"):
                await active_provider.aclose()
            if fallback_provider is not None and hasattr(fallback_provider, "aclose"):
                await fallback_provider.aclose()

        return progress

    async def poll(self) -> dict:
        """Refreshes Subtitlarr's local view of Bazarr's wanted list without
        starting any translation — safe to call just to populate dashboard
        stats and the queue table."""
        return await poller.poll_once(self._conn, self._get_client())

    async def warm_source_cache(self) -> dict:
        """Resolves source language/path for every pending item and
        pre-fetches its subtitle CONTENT into the local scratch cache —
        same read-side machinery run_batch() uses internally, but with NO
        translation, no provider calls, and no uploads. Lets the NAS's
        one disk-wake-up burst happen ahead of time, independent of when a
        translation run actually starts. Items resolve_and_gate() finds no
        usable source for are marked skipped_no_source exactly as they
        would be during a real run."""
        client = self._get_client()
        source_priority = repository.get_config(self._conn, "source_lang_priority", default=[])
        items = selector.get_full_translatable_queue(self._conn)
        ready_items = await selector.resolve_and_gate(self._conn, client, items, source_priority)
        cached_paths = await prefetch.prefetch_source_subtitles(
            client, ready_items, prefetch.DEFAULT_SCRATCH_ROOT
        )
        return {"resolved": len(ready_items), "cached": len(cached_paths)}

    async def run_now(self) -> RunProgress:
        await self.poll()
        items = selector.get_full_translatable_queue(self._conn)
        return await self.run_batch(items, triggered_by="manual_full")

    async def run_scheduled(self) -> RunProgress:
        await self.poll()
        items = selector.get_age_gated_queue(self._conn, self._settings.age_threshold_days)
        return await self.run_batch(items, triggered_by="scheduled")

    async def run_single_item(self, item_id: int) -> RunProgress:
        item = repository.get_item(self._conn, item_id)
        if item is None:
            raise ValueError(f"Item {item_id} not found")
        return await self.run_batch([item], triggered_by="manual_item", enforce_daily_limit=False)

    async def run_filtered(
        self, status: str | None, item_type: str | None, search: str | None
    ) -> RunProgress:
        """Runs every translatable item matching the given Queue-page filter
        (status/type/search) — e.g. 'all TV episodes', 'everything matching
        a title search'. Respects the normal daily cap/age gate like a
        scheduled run, since a large filtered set shouldn't bypass the
        GPU-load protections just because it was chosen explicitly."""
        items = selector.get_filtered_translatable_queue(
            self._conn, status=status, item_type=item_type, search=search
        )
        return await self.run_batch(items, triggered_by="manual_filtered")
