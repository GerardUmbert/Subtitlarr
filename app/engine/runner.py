import asyncio
import logging
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from app import state
from app.bazarr.client import BazarrClient
from app.config import Settings
from app.db import engine_instances_repo, repository
from app.engine import poller, prefetch, selector, translator
from app.providers import registry

logger = logging.getLogger(__name__)


class NoEngineConfiguredError(Exception):
    """Raised when a run is started but engine_instances has no enabled,
    non-rate-limited instance before the first separator (or no instances
    at all) — there's nothing left to translate with."""


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
    # monotonic() timestamps of the most recent completions, for a ROLLING
    # rate_per_min instead of a whole-run cumulative average — a cumulative
    # average stays skewed toward whatever was slow near the start of a
    # long run (a single 300s watchdog-timeout retry, or several
    # content-block-then-fallback items early on) for the ENTIRE rest of
    # the run, even once throughput has long since recovered. Bounded so a
    # very long run's memory doesn't grow unboundedly.
    _recent_completions: deque[float] = field(default_factory=lambda: deque(maxlen=20))
    # Set by RunController.cancel_current() (e.g. a "Stop" button on the
    # Queue/Dashboard page). Checked BETWEEN items, and also BETWEEN
    # BATCHES within the item currently translating (see translator.py's
    # cancel_check param) — a Stop click no longer has to wait for a large
    # multi-batch item to finish all its batches first. Whatever's already
    # in flight when the check fires (a single translate() call already
    # sent, or a whole concurrent window of them via asyncio.gather())
    # still completes — only the NEXT batch/window is skipped. The item
    # that was interrupted is marked 'failed' (a partial translation is
    # never uploaded), and every remaining not-yet-started item is left
    # untouched (still 'pending'/'queued').
    cancel_requested: bool = False

    def record_completion(self) -> None:
        self._recent_completions.append(time.monotonic())

    @property
    def rate_per_min(self) -> float:
        if len(self._recent_completions) < 2:
            # Not enough recent samples for a windowed rate yet — fall back
            # to the cumulative average so the UI shows SOMETHING early in
            # a run instead of blank/zero for the first couple of items.
            elapsed_min = (time.monotonic() - self.started_at) / 60
            return round(self.processed / elapsed_min, 1) if elapsed_min > 0 else 0.0
        span_min = (self._recent_completions[-1] - self._recent_completions[0]) / 60
        if span_min <= 0:
            return 0.0
        return round((len(self._recent_completions) - 1) / span_min, 1)

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
        with state.db_lock:
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
            with state.db_lock:
                already_done_today = repository.count_completed_today(self._conn)
            remaining = max(0, daily_limit - already_done_today)
            ready_items = ready_items[:remaining]

        with state.db_lock:
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

        # Each cascade instance already carries its own batch_token_budget/
        # concurrent_batch_window/num_ctx in config_json — no more global
        # per-provider-TYPE lookup tables here (an earlier version of this
        # code had one; engine_instances lets several instances of the
        # SAME type coexist with different tuning, so a single table keyed
        # by provider type can no longer express that).
        #
        # Rebuilt fresh for EVERY item (not once at run start) — an
        # instance can trip its 24h rate-limit cooldown mid-run (see
        # engine_instances_repo.record_rate_limited_failure), and a cascade
        # snapshot taken before that happened would otherwise keep sending
        # every remaining item's first attempt straight at an instance
        # already known to be dead, burning a guaranteed-to-fail request +
        # the one-retry wait on each one instead of skipping straight to
        # whatever's next. Confirmed live: after Gemini Main tripped, every
        # subsequent item still tried it first and waited out the retry
        # before falling to Gemini Secondary.
        def _build_cascade():
            with state.db_lock:
                cascade_instances = engine_instances_repo.get_cascade(self._conn)
            if not cascade_instances:
                return None
            cascade, name_to_instance_id = registry.build_cascade_providers(cascade_instances)
            active_config = cascade_instances[0]["config"]
            batch_token_budget_override, concurrent_batch_window = registry.batch_settings_for(
                active_config
            )
            num_ctx = active_config.get("num_ctx", 8192)
            return cascade, name_to_instance_id, batch_token_budget_override, concurrent_batch_window, num_ctx

        if _build_cascade() is None:
            with state.db_lock:
                repository.finish_run(self._conn, run_id, 0, 0)
            progress.active = False
            raise NoEngineConfiguredError(
                "No enabled, non-rate-limited engine instance is configured — "
                "add or re-enable one on the Engines page."
            )

        pause_seconds = self._settings.pause_between_items_seconds
        # EVERY provider object built across the whole run — a fresh
        # cascade, and therefore fresh provider objects each with their
        # OWN httpx client, is built per item (see above), so the same DB
        # instance can produce many distinct provider objects over a long
        # run. Every single one holds its own open connection pool and
        # must be aclose()'d — closing only the first one seen per
        # instance would leak the rest. Collected in a plain list instead
        # of closing per-item so a still-in-flight client from a
        # currently-failing item is never torn down out from under it;
        # everything is closed together, once, in the outer finally block
        # below.
        all_providers_built: list = []

        def _on_call_result_for(name_to_instance_id: dict) -> Callable:
            def _on_call_result(provider, rate_limited: bool) -> None:
                instance_id = name_to_instance_id.get(provider.name)
                if instance_id is None:
                    return
                if rate_limited:
                    with state.db_lock:
                        engine_instances_repo.record_rate_limited_failure(self._conn, instance_id)
                else:
                    with state.db_lock:
                        engine_instances_repo.record_success(self._conn, instance_id)

            return _on_call_result

        try:
            for i, entry in enumerate(ready_items):
                if progress.cancel_requested:
                    logger.info(
                        "Run %s cancelled after %d/%d items — %d item(s) left untouched",
                        run_id, progress.processed, progress.total, len(ready_items) - i,
                    )
                    break

                built = _build_cascade()
                if built is None:
                    # Every instance is now disabled/rate-limited — this can
                    # only happen mid-run (the pre-loop check above already
                    # guaranteed at least one was available at the start).
                    # Remaining items are left untouched, same as a cancel.
                    logger.warning(
                        "Run %s: no engine instance available anymore (all rate-limited or "
                        "disabled) — stopping with %d/%d items left untouched",
                        run_id, len(ready_items) - i, progress.total,
                    )
                    break
                cascade, name_to_instance_id, batch_token_budget_override, concurrent_batch_window, num_ctx = built
                all_providers_built.extend(cascade)

                item_id = entry["item"]["id"]
                cached_path = cached_paths.get(item_id)
                try:
                    await translator.translate_item(
                        self._conn,
                        client,
                        entry["item"],
                        entry["source_lang"],
                        entry["source_path"],
                        cascade,
                        run_id,
                        num_ctx=num_ctx,
                        batch_token_budget_override=batch_token_budget_override,
                        cached_source_path=cached_path,
                        queue_uploads=self._settings.queue_uploads_enabled,
                        retry_pause_seconds=pause_seconds,
                        concurrent_batch_window=concurrent_batch_window,
                        on_call_result=_on_call_result_for(name_to_instance_id),
                        cancel_check=lambda: progress.cancel_requested,
                    )
                except translator.RunCancelledError:
                    # Cancelled partway through THIS item's own batches
                    # (not just between items) — the item is already
                    # marked 'failed' by translate_item itself. Counts
                    # toward processed/failed like any other outcome, then
                    # the run stops here rather than moving on to the next
                    # item, same as a between-items cancel already did.
                    progress.failed += 1
                    progress.processed += 1
                    progress.record_completion()
                    logger.info(
                        "Run %s cancelled mid-item (item %s) — %d item(s) left untouched",
                        run_id, item_id, len(ready_items) - i - 1,
                    )
                    break
                except Exception:  # noqa: BLE001 - one item's failure must not abort the batch
                    progress.failed += 1
                    progress.processed += 1
                    progress.record_completion()
                    logger.exception("Translation failed for item %s", item_id)
                else:
                    # Only clean up the cached source on SUCCESS — a failed
                    # item keeps its cached file so a retry can reuse it
                    # without re-fetching from Bazarr, and so the failure
                    # can be investigated against the exact source that
                    # caused it.
                    prefetch.cleanup_scratch_file(cached_path)
                    progress.processed += 1
                    progress.record_completion()
                # A short rest between items so a long batch doesn't peg the
                # GPU non-stop for hours — skipped after the last item.
                if pause_seconds > 0 and i + 1 < len(ready_items):
                    await asyncio.sleep(pause_seconds)
        finally:
            progress.active = False
            with state.db_lock:
                repository.finish_run(self._conn, run_id, progress.processed, progress.failed)
            for provider in all_providers_built:
                if hasattr(provider, "aclose"):
                    await provider.aclose()

        return progress

    def cancel_current(self) -> bool:
        """Requests that the currently active run stop after its in-flight
        item finishes (or fails) — never mid-item. Returns False if no run
        is active, so the caller (the API route) can tell the difference
        between "cancelled" and "nothing to cancel" rather than silently
        no-op'ing either way."""
        if self.current is None or not self.current.active:
            return False
        self.current.cancel_requested = True
        return True

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
        with state.db_lock:
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
        with state.db_lock:
            item = repository.get_item(self._conn, item_id)
        if item is None:
            raise ValueError(f"Item {item_id} not found")
        return await self.run_batch([item], triggered_by="manual_item", enforce_daily_limit=False)

    async def run_by_ids(self, item_ids: list[int]) -> RunProgress:
        """Runs an explicit, caller-chosen set of items as ONE batch/
        run_history row — for filters the Queue page's status/type/search
        params can't express (e.g. "every item translating INTO Spanish,
        regardless of title"), where the caller already knows exactly
        which item ids it wants without needing a DB-side WHERE clause.
        Missing ids are silently skipped (an id list built from a stale
        page could reference a since-deleted item) rather than failing
        the whole batch over one bad id. Bypasses the daily limit, same
        as run_single_item — an explicit hand-picked list is exactly the
        kind of deliberate one-off the cap isn't meant to block."""
        with state.db_lock:
            items = [
                item for item_id in item_ids
                if (item := repository.get_item(self._conn, item_id)) is not None
            ]
        return await self.run_batch(items, triggered_by="manual_filtered", enforce_daily_limit=False)

    async def run_filtered(
        self,
        status: str | None,
        item_type: str | None,
        search: str | None,
        model: str | None = None,
    ) -> RunProgress:
        """Runs every translatable item matching the given Queue-page filter
        (status/type/search/model) — e.g. 'all TV episodes', 'everything
        matching a title search', 'everything translated by a specific
        model' (for re-running items a weaker fallback model produced).
        Respects the normal daily cap/age gate like a scheduled run, since
        a large filtered set shouldn't bypass the GPU-load protections just
        because it was chosen explicitly."""
        items = selector.get_filtered_translatable_queue(
            self._conn, status=status, item_type=item_type, search=search, model=model
        )
        return await self.run_batch(items, triggered_by="manual_filtered")
