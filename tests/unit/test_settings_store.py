import pytest

from app.config import Settings
from app.db import database, settings_store


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def test_saved_settings_survive_simulated_restart(conn):
    """Regression test: Bazarr URL/key, engine config, and schedule config
    must persist across restarts. Previously these only lived in the
    in-memory Settings object and were silently lost on every restart."""
    settings_store.save_one(conn, "bazarr_base_url", "http://192.168.1.215:6767")
    settings_store.save_one(conn, "bazarr_api_key", "mykey123")
    settings_store.save_one(conn, "ollama_base_url", "http://192.168.1.215:11434")
    settings_store.save_one(conn, "ollama_model", "gemma3:4b")
    settings_store.save_one(conn, "age_threshold_days", 21)

    # Simulate a fresh process: brand-new Settings() with only env-var
    # defaults, then load DB overrides on top — exactly what main.py does
    # on startup.
    fresh_settings = Settings()
    assert fresh_settings.bazarr_base_url == ""  # confirms defaults alone don't have it

    settings_store.load_into(conn, fresh_settings)

    assert fresh_settings.bazarr_base_url == "http://192.168.1.215:6767"
    assert fresh_settings.bazarr_api_key == "mykey123"
    assert fresh_settings.ollama_base_url == "http://192.168.1.215:11434"
    assert fresh_settings.ollama_model == "gemma3:4b"
    assert fresh_settings.age_threshold_days == 21


def test_unsaved_settings_keep_env_defaults(conn):
    fresh_settings = Settings()
    settings_store.load_into(conn, fresh_settings)
    assert fresh_settings.schedule_cron == "0 3 * * *"  # untouched default survives


def test_save_one_rejects_unknown_key(conn):
    with pytest.raises(ValueError):
        settings_store.save_one(conn, "db_path", "/somewhere/else")
