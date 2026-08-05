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


@pytest.mark.asyncio
async def test_warm_source_cache_resolves_and_prefetches_without_translating(conn, monkeypatch):
    """warm_source_cache() must resolve source language/path and prefetch
    content — same read-side machinery run_batch() uses — but must NEVER
    touch translate_item() or any provider, since this is purely a
    cache-warming action."""
    items = _seed_pending_items(conn, 2)

    async def fake_resolve_and_gate(conn, client, items, source_priority):
        return [{"item": item, "source_lang": "en", "source_path": f"/{item['id']}.srt"} for item in items]

    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", fake_resolve_and_gate)

    prefetch_calls = []

    async def fake_prefetch(client, ready_items, scratch_dir):
        prefetch_calls.append((ready_items, scratch_dir))
        return {entry["item"]["id"]: scratch_dir / f"{entry['item']['id']}.srt" for entry in ready_items}

    monkeypatch.setattr(runner_module.prefetch, "prefetch_source_subtitles", fake_prefetch)

    translate_called = {"count": 0}

    async def fake_translate_item(*args, **kwargs):
        translate_called["count"] += 1

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    controller = RunController(conn, lambda: object(), Settings())
    result = await controller.warm_source_cache()

    assert result == {"resolved": 2, "cached": 2}
    assert len(prefetch_calls) == 1
    assert translate_called["count"] == 0  # never translates anything


@pytest.mark.asyncio
async def test_warm_source_cache_marks_no_source_items(conn, monkeypatch):
    """Items resolve_and_gate() can't find a source for must still be
    marked skipped_no_source, exactly as a real run would — the caching
    action shares the same resolution logic, not a separate path."""
    items = _seed_pending_items(conn, 1)

    async def fake_resolve_and_gate(conn, client, items, source_priority):
        repository.mark_skipped_no_source(conn, items[0]["id"])
        return []

    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", fake_resolve_and_gate)

    async def fake_prefetch(client, ready_items, scratch_dir):
        return {}

    monkeypatch.setattr(runner_module.prefetch, "prefetch_source_subtitles", fake_prefetch)

    controller = RunController(conn, lambda: object(), Settings())
    result = await controller.warm_source_cache()

    assert result == {"resolved": 0, "cached": 0}
    row = conn.execute("SELECT status FROM items WHERE id = ?", (items[0]["id"],)).fetchone()
    assert row["status"] == "skipped_no_source"
