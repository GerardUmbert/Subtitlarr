import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Callable

from app.bazarr.client import BazarrClient
from app.config import Settings
from app.db import repository
from app.engine import poller, selector, translator
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

        active_provider = get_active_provider(self._settings)
        fallback_provider = get_fallback_provider(self._settings)

        pause_seconds = self._settings.pause_between_items_seconds

        # Each engine's batch-size settings are tuned for fundamentally
        # different constraints: Ollama's small default protects local
        # VRAM/GPU, while NVIDIA's cloud model has no such limit and was
        # confirmed live to reliably handle a full ~400-cue episode in one
        # request. Sharing one budget between them (as an earlier version
        # of this code did) meant NVIDIA silently inherited Ollama's
        # GPU-safe default and ran far more sequential batches than it
        # needed to.
        if active_provider.name == "nvidia":
            batch_token_budget_override = self._settings.nvidia_batch_token_budget
        else:
            batch_token_budget_override = self._settings.ollama_batch_token_budget

        try:
            for i, entry in enumerate(ready_items):
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
                    )
                except Exception:  # noqa: BLE001 - one item's failure must not abort the batch
                    progress.failed += 1
                    logger.exception("Translation failed for item %s", entry["item"]["id"])
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
