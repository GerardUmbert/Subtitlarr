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


@pytest.mark.asyncio
async def test_nvidia_engine_uses_nvidia_batch_budget_not_ollamas(conn, monkeypatch):
    """Regression test: an earlier version of run_batch always passed
    settings.ollama_batch_token_budget into translate_item() regardless of
    which engine was actually active, so a configured NVIDIA engine
    silently inherited Ollama's small GPU-safe default (900) instead of
    NVIDIA's own — confirmed live that NVIDIA's cloud model reliably
    handles much larger batches (400 cues in one request) with no local
    hardware constraint driving a small default."""
    items = _seed_pending_item(conn)
    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", _fake_resolve_and_gate)

    class FakeNvidiaProvider:
        name = "nvidia"

    monkeypatch.setattr(runner_module, "get_active_provider", lambda settings: FakeNvidiaProvider())
    monkeypatch.setattr(runner_module, "get_fallback_provider", lambda settings: None)

    captured_kwargs = {}

    async def fake_translate_item(conn, client, item, *args, **kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    settings = Settings(
        ollama_batch_token_budget=900,
        nvidia_batch_token_budget=12000,
        pause_between_items_seconds=0,
    )
    controller = RunController(conn, lambda: object(), settings)

    await controller.run_batch(items, triggered_by="manual_full")

    assert captured_kwargs["batch_token_budget_override"] == 12000


@pytest.mark.asyncio
async def test_openrouter_engine_uses_openrouter_batch_budget_not_ollamas(conn, monkeypatch):
    """Same regression as the NVIDIA test above, for OpenRouter: it must
    get its own openrouter_batch_token_budget and
    openrouter_concurrent_batch_window, not silently inherit Ollama's
    small GPU-safe defaults."""
    items = _seed_pending_item(conn)
    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", _fake_resolve_and_gate)

    class FakeOpenRouterProvider:
        name = "openrouter"

    monkeypatch.setattr(runner_module, "get_active_provider", lambda settings: FakeOpenRouterProvider())
    monkeypatch.setattr(runner_module, "get_fallback_provider", lambda settings: None)

    captured_kwargs = {}

    async def fake_translate_item(conn, client, item, *args, **kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    settings = Settings(
        ollama_batch_token_budget=900,
        openrouter_batch_token_budget=4000,
        openrouter_concurrent_batch_window=3,
        pause_between_items_seconds=0,
    )
    controller = RunController(conn, lambda: object(), settings)

    await controller.run_batch(items, triggered_by="manual_full")

    assert captured_kwargs["batch_token_budget_override"] == 4000
    assert captured_kwargs["concurrent_batch_window"] == 3


@pytest.mark.asyncio
async def test_llamacpp_engine_uses_llamacpp_batch_budget_not_ollamas(conn, monkeypatch):
    """Same regression as the NVIDIA/OpenRouter tests above, for
    llama.cpp: it must get its own llamacpp_batch_token_budget, not
    silently inherit Ollama's. Also confirms it stays non-concurrent
    (concurrent_batch_window == 1) despite being wired up alongside cloud
    providers — it's a local runtime, same reasoning as Ollama."""
    items = _seed_pending_item(conn)
    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", _fake_resolve_and_gate)

    class FakeLlamaCppProvider:
        name = "llamacpp"

    monkeypatch.setattr(runner_module, "get_active_provider", lambda settings: FakeLlamaCppProvider())
    monkeypatch.setattr(runner_module, "get_fallback_provider", lambda settings: None)

    captured_kwargs = {}

    async def fake_translate_item(conn, client, item, *args, **kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    settings = Settings(
        ollama_batch_token_budget=900,
        llamacpp_batch_token_budget=350,
        pause_between_items_seconds=0,
    )
    controller = RunController(conn, lambda: object(), settings)

    await controller.run_batch(items, triggered_by="manual_full")

    assert captured_kwargs["batch_token_budget_override"] == 350
    assert captured_kwargs["concurrent_batch_window"] == 1


@pytest.mark.asyncio
async def test_ollama_engine_still_uses_ollama_batch_budget(conn, monkeypatch):
    items = _seed_pending_item(conn)
    monkeypatch.setattr(runner_module.selector, "resolve_and_gate", _fake_resolve_and_gate)

    class FakeOllamaProvider:
        name = "ollama"

    monkeypatch.setattr(runner_module, "get_active_provider", lambda settings: FakeOllamaProvider())
    monkeypatch.setattr(runner_module, "get_fallback_provider", lambda settings: None)

    captured_kwargs = {}

    async def fake_translate_item(conn, client, item, *args, **kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(runner_module.translator, "translate_item", fake_translate_item)

    settings = Settings(
        ollama_batch_token_budget=900,
        nvidia_batch_token_budget=12000,
        pause_between_items_seconds=0,
    )
    controller = RunController(conn, lambda: object(), settings)

    await controller.run_batch(items, triggered_by="manual_full")

    assert captured_kwargs["batch_token_budget_override"] == 900
