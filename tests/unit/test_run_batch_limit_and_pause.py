import pytest

from app.config import Settings
from app.db import database, repository
from app.engine import runner as runner_module
from app.engine.runner import RunController


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def _seed_pending_items(conn, count: int) -> list:
    for i in range(count):
        repository.upsert_item_seen(
            conn, item_type="movie", bazarr_id=i, series_id=None,
            title=f"Movie {i}", series_title=None, season_episode=None,
            target_language="it",
        )
    return conn.execute("SELECT * FROM items ORDER BY bazarr_id ASC").fetchall()


@pytest.fixture(autouse=True)
def stub_providers(monkeypatch):
    """run_batch always calls get_active_provider/get_fallback_provider and
    tries to aclose() them — stub with something inert so tests don't need
    real engine config."""
    class FakeProvider:
        name = "fake"

    monkeypatch.setattr(runner_module, "get_active_provider", lambda settings: FakeProvider())
    monkeypatch.setattr(runner_module, "get_fallback_provider", lambda settings: None)


@pytest.mark.asyncio
async def test_daily_limit_caps_a_full_queue_run(conn, monkeypatch):
    """Regression test: a 1400-item backlog at ~2.5min/item is ~58 hours of
    straight work. daily_translation_limit must stop dispatching new items
    once the cap for the day is reached, rather than draining the whole
    queue in one run."""
    items = _seed_pending_items(conn, 10)

    async def fake_resolve_and_gate(conn, client, items, source_priority):
        return [{"item": item, "source_lang": "en", "source_path": "/x.srt"} for item in items]

    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", fake_resolve_and_gate)

    translated_ids = []

    async def fake_translate_item(conn, client, item, *args, **kwargs):
        translated_ids.append(item["id"])

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    settings = Settings(daily_translation_limit=3, pause_between_items_seconds=0)
    controller = RunController(conn, lambda: object(), settings)

    progress = await controller.run_batch(items, triggered_by="scheduled")

    assert progress.total == 3
    assert len(translated_ids) == 3


@pytest.mark.asyncio
async def test_daily_limit_accounts_for_items_already_done_today(conn, monkeypatch):
    for i in range(2):
        repository.upsert_item_seen(
            conn, item_type="movie", bazarr_id=100 + i, series_id=None,
            title=f"Done {i}", series_title=None, season_episode=None,
            target_language="it",
        )
    done_rows = conn.execute(
        "SELECT id FROM items WHERE bazarr_id IN (100, 101)"
    ).fetchall()
    for row in done_rows:
        repository.update_item_status(conn, row["id"], "done", mark_completed=True)

    pending_items = _seed_pending_items(conn, 5)

    async def fake_resolve_and_gate(conn, client, items, source_priority):
        return [{"item": item, "source_lang": "en", "source_path": "/x.srt"} for item in items]

    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", fake_resolve_and_gate)

    translated_ids = []

    async def fake_translate_item(conn, client, item, *args, **kwargs):
        translated_ids.append(item["id"])

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    settings = Settings(daily_translation_limit=3, pause_between_items_seconds=0)
    controller = RunController(conn, lambda: object(), settings)

    progress = await controller.run_batch(pending_items, triggered_by="scheduled")

    # 2 already done today, cap is 3 -> only 1 more should run this batch
    assert progress.total == 1
    assert len(translated_ids) == 1


@pytest.mark.asyncio
async def test_daily_limit_zero_means_unlimited(conn, monkeypatch):
    items = _seed_pending_items(conn, 5)

    async def fake_resolve_and_gate(conn, client, items, source_priority):
        return [{"item": item, "source_lang": "en", "source_path": "/x.srt"} for item in items]

    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", fake_resolve_and_gate)

    async def fake_translate_item(conn, client, item, *args, **kwargs):
        pass

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    settings = Settings(daily_translation_limit=0, pause_between_items_seconds=0)
    controller = RunController(conn, lambda: object(), settings)

    progress = await controller.run_batch(items, triggered_by="scheduled")
    assert progress.total == 5


@pytest.mark.asyncio
async def test_per_item_manual_run_bypasses_daily_limit(conn, monkeypatch):
    """A forced per-item re-run is an explicit one-off request and must
    always go through, even if the daily cap was already hit."""
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=1, series_id=None,
        title="Forced", series_title=None, season_episode=None,
        target_language="it",
    )
    item = conn.execute("SELECT * FROM items WHERE bazarr_id = 1").fetchone()

    async def fake_resolve_and_gate(conn, client, items, source_priority):
        return [{"item": item, "source_lang": "en", "source_path": "/x.srt"} for item in items]

    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", fake_resolve_and_gate)

    translated_ids = []

    async def fake_translate_item(conn, client, item, *args, **kwargs):
        translated_ids.append(item["id"])

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    # Cap already exhausted (0 remaining).
    settings = Settings(daily_translation_limit=1, pause_between_items_seconds=0)
    controller = RunController(conn, lambda: object(), settings)

    progress = await controller.run_single_item(item["id"])

    assert progress.total == 1
    assert len(translated_ids) == 1


@pytest.mark.asyncio
async def test_pause_between_items_sleeps_between_but_not_after_last(conn, monkeypatch):
    items = _seed_pending_items(conn, 3)

    async def fake_resolve_and_gate(conn, client, items, source_priority):
        return [{"item": item, "source_lang": "en", "source_path": "/x.srt"} for item in items]

    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", fake_resolve_and_gate)

    async def fake_translate_item(conn, client, item, *args, **kwargs):
        pass

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(runner_module.asyncio, "sleep", fake_sleep)

    settings = Settings(daily_translation_limit=0, pause_between_items_seconds=30)
    controller = RunController(conn, lambda: object(), settings)

    await controller.run_batch(items, triggered_by="manual_full")

    # 3 items -> 2 pauses (never after the last item)
    assert sleep_calls == [30, 30]


@pytest.mark.asyncio
async def test_pause_disabled_when_zero(conn, monkeypatch):
    items = _seed_pending_items(conn, 3)

    async def fake_resolve_and_gate(conn, client, items, source_priority):
        return [{"item": item, "source_lang": "en", "source_path": "/x.srt"} for item in items]

    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", fake_resolve_and_gate)

    async def fake_translate_item(conn, client, item, *args, **kwargs):
        pass

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(runner_module.asyncio, "sleep", fake_sleep)

    settings = Settings(daily_translation_limit=0, pause_between_items_seconds=0)
    controller = RunController(conn, lambda: object(), settings)

    await controller.run_batch(items, triggered_by="manual_full")

    assert sleep_calls == []
