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


def _seed_pending_item(conn) -> list:
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=1, series_id=None,
        title="Movie", series_title=None, season_episode=None,
        target_language="it",
    )
    return conn.execute("SELECT * FROM items").fetchall()


async def _fake_resolve_and_gate(conn, client, items, source_priority):
    return [{"item": item, "source_lang": "en", "source_path": "/x.srt"} for item in items]


def _stub_cascade(monkeypatch, provider_type: str, config: dict):
    """Regression coverage: each cascade instance carries its OWN
    batch_token_budget/concurrent_batch_window in its config — an earlier
    version of run_batch looked these up from a fixed table keyed by
    provider NAME (e.g. settings.nvidia_batch_token_budget), which meant a
    newly added provider type silently inherited Ollama's small GPU-safe
    default. engine_instances makes that impossible by construction: the
    value run_batch uses is whatever's actually stored on that instance's
    row, nothing inferred from its type."""
    _type = provider_type

    class FakeProvider:
        name = _type
        provider_type = _type

    fake_instance = {
        "id": 1, "name": provider_type, "provider_type": provider_type,
        "enabled": True, "config": config, "rate_limited_until": None,
    }
    monkeypatch.setattr(
        runner_module.engine_instances_repo, "get_cascade", lambda conn: [fake_instance]
    )
    monkeypatch.setattr(
        runner_module.registry,
        "build_cascade_providers",
        lambda instances: ([FakeProvider()], {provider_type: 1}),
    )


@pytest.mark.asyncio
async def test_nvidia_engine_uses_nvidia_batch_budget_not_ollamas(conn, monkeypatch):
    items = _seed_pending_item(conn)
    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", _fake_resolve_and_gate)
    _stub_cascade(
        monkeypatch, "nvidia",
        {"batch_token_budget": 12000, "concurrent_batch_window": 4, "api_key": "x", "model": "m"},
    )

    captured_kwargs = {}

    async def fake_translate_item(conn, client, item, cascade, *args, **kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    settings = Settings(pause_between_items_seconds=0)
    controller = RunController(conn, lambda: object(), settings)

    await controller.run_batch(items, triggered_by="manual_full")

    assert captured_kwargs["batch_token_budget_override"] == 12000
    assert captured_kwargs["concurrent_batch_window"] == 4


@pytest.mark.asyncio
async def test_openrouter_engine_uses_openrouter_batch_budget_not_ollamas(conn, monkeypatch):
    items = _seed_pending_item(conn)
    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", _fake_resolve_and_gate)
    _stub_cascade(
        monkeypatch, "openrouter",
        {"batch_token_budget": 4000, "concurrent_batch_window": 3, "api_key": "x", "model": "m"},
    )

    captured_kwargs = {}

    async def fake_translate_item(conn, client, item, cascade, *args, **kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    settings = Settings(pause_between_items_seconds=0)
    controller = RunController(conn, lambda: object(), settings)

    await controller.run_batch(items, triggered_by="manual_full")

    assert captured_kwargs["batch_token_budget_override"] == 4000
    assert captured_kwargs["concurrent_batch_window"] == 3


@pytest.mark.asyncio
async def test_llamacpp_engine_uses_llamacpp_batch_budget_not_ollamas(conn, monkeypatch):
    """Also confirms it stays non-concurrent (concurrent_batch_window == 1
    when absent from config) despite being wired up alongside cloud
    providers — it's a local runtime, same reasoning as Ollama."""
    items = _seed_pending_item(conn)
    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", _fake_resolve_and_gate)
    _stub_cascade(
        monkeypatch, "llamacpp",
        {"batch_token_budget": 350, "base_url": "http://x"},
    )

    captured_kwargs = {}

    async def fake_translate_item(conn, client, item, cascade, *args, **kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    settings = Settings(pause_between_items_seconds=0)
    controller = RunController(conn, lambda: object(), settings)

    await controller.run_batch(items, triggered_by="manual_full")

    assert captured_kwargs["batch_token_budget_override"] == 350
    assert captured_kwargs["concurrent_batch_window"] == 1


@pytest.mark.asyncio
async def test_ollama_engine_uses_its_own_batch_budget(conn, monkeypatch):
    items = _seed_pending_item(conn)
    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", _fake_resolve_and_gate)
    _stub_cascade(
        monkeypatch, "ollama",
        {"batch_token_budget": 900, "base_url": "http://x", "model": "m"},
    )

    captured_kwargs = {}

    async def fake_translate_item(conn, client, item, cascade, *args, **kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    settings = Settings(pause_between_items_seconds=0)
    controller = RunController(conn, lambda: object(), settings)

    await controller.run_batch(items, triggered_by="manual_full")

    assert captured_kwargs["batch_token_budget_override"] == 900
