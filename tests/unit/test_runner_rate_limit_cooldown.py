"""Confirms run_batch actually wires translate_item's on_call_result
callback through to engine_instances_repo.record_rate_limited_failure/
record_success — i.e. that a real DB row's rate-limit cooldown state
changes as a consequence of running a batch, not just that the callback
function exists in isolation (already covered by
test_engine_instances_repo.py) or that translator.py calls it
(test_concurrent_nvidia_batches.py checks the callback fires, with a
plain function, not a DB-backed one)."""
import pytest

from app.config import Settings
from app.db import database, engine_instances_repo, repository
from app.engine import runner as runner_module
from app.engine.runner import RunController


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def _seed_pending_item(conn) -> list:
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=1, series_id=None,
        title="Movie", series_title=None, season_episode=None,
        target_language="it",
    )
    return conn.execute("SELECT * FROM items").fetchall()


async def _fake_resolve_and_gate(conn, client, items, source_priority):
    return [{"item": item, "source_lang": "en", "source_path": "/x.srt"} for item in items]


def _expire_burst_debounce(conn, instance_id: int) -> None:
    """record_rate_limited_failure() debounces failures within
    BURST_DEBOUNCE_SECONDS of each other into a single strike (a
    concurrent burst hitting a short-window rate limit isn't sustained
    exhaustion — see engine_instances_repo's docstring). Tests that want
    to exercise multiple SEPARATE strikes need to fast-forward
    last_failure_at themselves rather than relying on real elapsed wall-
    clock time between fast, in-process run_batch() calls."""
    from datetime import datetime, timedelta, timezone

    past = (
        datetime.now(timezone.utc)
        - timedelta(seconds=engine_instances_repo.BURST_DEBOUNCE_SECONDS + 1)
    ).isoformat()
    with conn:
        conn.execute(
            "UPDATE engine_instances SET last_failure_at = ? WHERE id = ?", (past, instance_id)
        )


@pytest.mark.asyncio
async def test_run_batch_records_rate_limited_failure_against_the_real_instance_row(
    conn, monkeypatch
):
    instance = engine_instances_repo.create_instance(
        conn, name="gemini", provider_type="gemini", config={"api_key": "x", "model": "m"}
    )
    items = _seed_pending_item(conn)
    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", _fake_resolve_and_gate)

    async def fake_translate_item(conn, client, item, source_lang, source_path, cascade, run_id, **kwargs):
        # Simulate translator.py calling back with a rate-limited failure
        # against the primary cascade instance, same shape _call_provider
        # actually uses.
        kwargs["on_call_result"](cascade[0], True)
        raise RuntimeError("simulated failure so the item doesn't count as done")

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    settings = Settings(pause_between_items_seconds=0)
    controller = RunController(conn, lambda: object(), settings)

    await controller.run_batch(items, triggered_by="manual_full")

    updated = engine_instances_repo.get_instance(conn, instance["id"])
    assert updated["consecutive_failures"] == 1


@pytest.mark.asyncio
async def test_run_batch_trips_cooldown_after_threshold_consecutive_failures(conn, monkeypatch):
    instance = engine_instances_repo.create_instance(
        conn, name="gemini", provider_type="gemini", config={"api_key": "x", "model": "m"}
    )
    # One item per attempt — run_batch once per failure so each call
    # independently records against the same instance row, same as
    # several real translation attempts across a run/several runs would.
    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", _fake_resolve_and_gate)

    async def fake_translate_item(conn, client, item, source_lang, source_path, cascade, run_id, **kwargs):
        kwargs["on_call_result"](cascade[0], True)
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    settings = Settings(pause_between_items_seconds=0)
    controller = RunController(conn, lambda: object(), settings)

    for _ in range(engine_instances_repo.RATE_LIMIT_FAILURE_THRESHOLD):
        _expire_burst_debounce(conn, instance["id"])
        items = _seed_pending_item(conn)
        await controller.run_batch(items, triggered_by="manual_full")

    updated = engine_instances_repo.get_instance(conn, instance["id"])
    assert updated["rate_limited_until"] is not None
    # And the cascade builder now genuinely excludes it.
    assert engine_instances_repo.get_cascade(conn) == []


@pytest.mark.asyncio
async def test_run_batch_records_success_and_resets_counter(conn, monkeypatch):
    instance = engine_instances_repo.create_instance(
        conn, name="gemini", provider_type="gemini", config={"api_key": "x", "model": "m"}
    )
    engine_instances_repo.record_rate_limited_failure(conn, instance["id"])
    assert engine_instances_repo.get_instance(conn, instance["id"])["consecutive_failures"] == 1

    items = _seed_pending_item(conn)
    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", _fake_resolve_and_gate)

    async def fake_translate_item(conn, client, item, source_lang, source_path, cascade, run_id, **kwargs):
        kwargs["on_call_result"](cascade[0], False)  # success

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    settings = Settings(pause_between_items_seconds=0)
    controller = RunController(conn, lambda: object(), settings)
    await controller.run_batch(items, triggered_by="manual_full")

    updated = engine_instances_repo.get_instance(conn, instance["id"])
    assert updated["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_run_batch_raises_when_no_cascade_configured(conn, monkeypatch):
    """With zero enabled/non-rate-limited instances, run_batch must fail
    clearly instead of crashing on an empty cascade[0] index error."""
    items = _seed_pending_item(conn)
    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", _fake_resolve_and_gate)

    settings = Settings(pause_between_items_seconds=0)
    controller = RunController(conn, lambda: object(), settings)

    with pytest.raises(runner_module.NoEngineConfiguredError):
        await controller.run_batch(items, triggered_by="manual_full")


def _seed_pending_items(conn, count: int) -> list:
    for i in range(count):
        repository.upsert_item_seen(
            conn, item_type="movie", bazarr_id=i, series_id=None,
            title=f"Movie {i}", series_title=None, season_episode=None,
            target_language="it",
        )
    return conn.execute("SELECT * FROM items ORDER BY bazarr_id ASC").fetchall()


@pytest.mark.asyncio
async def test_cascade_is_rebuilt_per_item_so_a_mid_run_trip_is_seen_immediately(
    conn, monkeypatch
):
    """Regression test for a real live incident: Gemini Main tripped its
    rate-limit cooldown partway through a run, but every SUBSEQUENT item
    still tried it first and paid for a guaranteed-to-fail request + the
    one-retry wait before falling to Gemini Secondary — because the
    cascade was built ONCE at run_batch() start and never re-checked.
    Confirms the primary instance actually used per item changes the
    moment its row trips, within the SAME run, not just across runs."""
    primary = engine_instances_repo.create_instance(
        conn, name="primary", provider_type="gemini", config={"api_key": "x", "model": "m"}
    )
    engine_instances_repo.create_instance(
        conn, name="secondary", provider_type="gemini", config={"api_key": "y", "model": "m"}
    )
    items = _seed_pending_items(conn, engine_instances_repo.RATE_LIMIT_FAILURE_THRESHOLD + 1)
    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", _fake_resolve_and_gate)

    primary_names_used = []

    async def fake_translate_item(conn, client, item, source_lang, source_path, cascade, run_id, **kwargs):
        primary_names_used.append(cascade[0].name)
        if cascade[0].name == "primary":
            # Real items are seconds/minutes apart, not milliseconds —
            # force the debounce window to have already elapsed so this
            # loop actually exercises separate strikes, the same way
            # test_run_batch_trips_cooldown_after_threshold_consecutive_
            # failures does across separate run_batch() calls.
            _expire_burst_debounce(conn, primary["id"])
            kwargs["on_call_result"](cascade[0], True)  # simulate a 429 against primary
            raise RuntimeError("simulated rate limit")
        # secondary succeeds — nothing else to do

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    settings = Settings(pause_between_items_seconds=0)
    controller = RunController(conn, lambda: object(), settings)
    await controller.run_batch(items, triggered_by="manual_full")

    # Primary should have been tried as cascade[0] for exactly
    # RATE_LIMIT_FAILURE_THRESHOLD items (until it tripped), then
    # secondary for every item after that, WITHIN this one run — not
    # primary for every single item.
    threshold = engine_instances_repo.RATE_LIMIT_FAILURE_THRESHOLD
    assert primary_names_used[:threshold] == ["primary"] * threshold
    assert primary_names_used[threshold:] == ["secondary"] * (len(items) - threshold)

    updated_primary = engine_instances_repo.get_instance(conn, primary["id"])
    assert updated_primary["rate_limited_until"] is not None


@pytest.mark.asyncio
async def test_run_batch_stops_cleanly_when_every_instance_trips_mid_run(conn, monkeypatch):
    """If EVERY cascade instance ends up rate-limited partway through a
    run (not just the primary), the loop must stop rather than crash on
    an empty cascade, leaving remaining items untouched (still pending),
    not marked failed."""
    only = engine_instances_repo.create_instance(
        conn, name="only", provider_type="gemini", config={"api_key": "x", "model": "m"}
    )
    items = _seed_pending_items(conn, engine_instances_repo.RATE_LIMIT_FAILURE_THRESHOLD + 2)
    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", _fake_resolve_and_gate)

    async def fake_translate_item(conn, client, item, source_lang, source_path, cascade, run_id, **kwargs):
        _expire_burst_debounce(conn, only["id"])
        kwargs["on_call_result"](cascade[0], True)
        raise RuntimeError("simulated rate limit")

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    settings = Settings(pause_between_items_seconds=0)
    controller = RunController(conn, lambda: object(), settings)
    progress = await controller.run_batch(items, triggered_by="manual_full")

    # Stopped after the instance tripped — not every item was attempted.
    assert progress.processed < len(items)
    assert engine_instances_repo.get_cascade(conn) == []


@pytest.mark.asyncio
async def test_cancel_current_stops_the_run_after_the_in_flight_item(conn, monkeypatch):
    engine_instances_repo.create_instance(
        conn, name="gemini", provider_type="gemini", config={"api_key": "x", "model": "m"}
    )
    items = _seed_pending_items(conn, 5)
    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", _fake_resolve_and_gate)

    controller = RunController(conn, lambda: object(), Settings(pause_between_items_seconds=0))

    call_count = 0

    async def fake_translate_item(conn, client, item, source_lang, source_path, cascade, run_id, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            # Simulate a "Stop" click arriving while the 2nd item is
            # in-flight — it must still finish this item normally, then
            # stop before starting a 3rd.
            controller.cancel_current()

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    progress = await controller.run_batch(items, triggered_by="manual_full")

    assert call_count == 2
    assert progress.processed == 2
    assert progress.cancel_requested is True


def test_cancel_current_returns_false_when_no_run_is_active(conn):
    controller = RunController(conn, lambda: object(), Settings())
    assert controller.cancel_current() is False


@pytest.mark.asyncio
async def test_cancel_mid_item_stops_the_run_without_starting_the_next_item(conn, monkeypatch):
    """A Stop click arriving mid-item (simulated here by translate_item
    itself raising RunCancelledError, which is what happens once
    translator.py's cancel_check trips between batches) must count that
    item as processed/failed, then stop the run — not treat it like an
    ordinary per-item failure that moves on to the next item."""
    from app.engine import translator as translator_module

    engine_instances_repo.create_instance(
        conn, name="gemini", provider_type="gemini", config={"api_key": "x", "model": "m"}
    )
    items = _seed_pending_items(conn, 5)
    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", _fake_resolve_and_gate)

    controller = RunController(conn, lambda: object(), Settings(pause_between_items_seconds=0))

    call_count = 0

    async def fake_translate_item(conn, client, item, source_lang, source_path, cascade, run_id, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise translator_module.RunCancelledError("cancelled mid-batch")

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    progress = await controller.run_batch(items, triggered_by="manual_full")

    assert call_count == 2  # never reached items 3-5
    assert progress.processed == 2
    assert progress.failed == 1
    assert progress.cancel_requested is False  # this test never actually called cancel_current()
