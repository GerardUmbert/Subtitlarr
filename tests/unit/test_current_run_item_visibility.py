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
    class FakeProvider:
        name = "fake"
        provider_type = "fake"

    fake_instance = {
        "id": 1, "name": "fake", "provider_type": "fake", "enabled": True,
        "config": {}, "rate_limited_until": None,
    }
    monkeypatch.setattr(
        runner_module.engine_instances_repo, "get_cascade", lambda conn: [fake_instance]
    )
    monkeypatch.setattr(
        runner_module.registry,
        "build_cascade_providers",
        lambda instances: ([FakeProvider()], {"fake": 1}),
    )
    monkeypatch.setattr(runner_module.registry, "batch_settings_for", lambda config: (0, 1))


@pytest.mark.asyncio
async def test_run_progress_captures_all_item_ids_including_not_yet_started(conn, monkeypatch):
    """Regression test: the Queue page's 'current batch' view only showed
    the single item actively translating, not items still queued waiting
    their turn — because item_run_log only gains a row on a TERMINAL
    outcome and items.status only reaches 'translating' once its turn in
    the sequential loop arrives, so a DB-only reconstruction of 'items in
    this run' missed every not-yet-started item. RunProgress.item_ids must
    capture the full set up front, at run_batch() start, not rely on
    querying the DB for evidence that doesn't exist yet."""
    items = _seed_pending_items(conn, 3)

    async def fake_resolve_and_gate(conn, client, items, source_priority):
        return [{"item": item, "source_lang": "en", "source_path": "/x.srt"} for item in items]

    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", fake_resolve_and_gate)

    # Only the FIRST item ever actually starts translating during this
    # test (the other two remain 'pending' the whole time, simulating
    # them still waiting their turn in the sequential loop).
    async def fake_translate_item(conn, client, item, *args, **kwargs):
        if item["id"] == items[0]["id"]:
            repository.update_item_status(conn, item["id"], "translating", mark_attempt=True)

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    settings = Settings(pause_between_items_seconds=0)
    controller = RunController(conn, lambda: object(), settings)

    # Capture progress.item_ids as it exists mid-run (before run_batch
    # returns) by wrapping translate_item to snapshot it on first call.
    captured = {}

    original_fake = fake_translate_item

    async def snapshotting_translate_item(conn, client, item, *args, **kwargs):
        if not captured:
            captured["item_ids"] = list(controller.current.item_ids)
        await original_fake(conn, client, item, *args, **kwargs)

    monkeypatch.setattr(runner_module.translator, "translate_item", snapshotting_translate_item)

    await controller.run_batch(items, triggered_by="manual_full")

    all_ids = {item["id"] for item in items}
    assert set(captured["item_ids"]) == all_ids

    # And critically: list_items_by_ids must return ALL of them, including
    # the two still 'pending' (never touched item_run_log, never reached
    # 'translating') — this is the actual query the Queue page's
    # /api/queue/current-run endpoint uses.
    rows = repository.list_items_by_ids(conn, list(all_ids))
    assert {row["id"] for row in rows} == all_ids
    statuses = {row["id"]: row["status"] for row in rows}
    assert statuses[items[0]["id"]] == "translating"
    assert statuses[items[1]["id"]] == "pending"
    assert statuses[items[2]["id"]] == "pending"


def test_list_items_by_ids_empty_list_returns_empty(conn):
    assert repository.list_items_by_ids(conn, []) == []
