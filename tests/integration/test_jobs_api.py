import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import database, repository
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "bazarr_base_url", "http://bazarr.test:6767")
    monkeypatch.setattr(settings, "bazarr_api_key", "testkey")
    with TestClient(app) as c:
        yield c


def test_get_jobs_reports_cron_and_run_state(client):
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cron_expression"] == settings.schedule_cron
    assert body["age_threshold_days"] == settings.age_threshold_days
    assert body["run_active"] is False


def test_run_now_starts_the_scheduled_job(client, monkeypatch):
    from app.engine.runner import RunController

    called = {"count": 0}

    async def fake_run_scheduled(self):
        called["count"] += 1

    monkeypatch.setattr(RunController, "run_scheduled", fake_run_scheduled)

    resp = client.post("/api/jobs/run-now")
    assert resp.status_code == 200
    assert resp.json()["started"] is True


def test_run_now_refuses_when_a_run_is_already_active(client, monkeypatch):
    from app.engine.runner import RunController, RunProgress

    def fake_current(self):
        return RunProgress(active=True)

    # runner.current is a plain attribute set during run_batch; simulate an
    # active run by setting it directly via the singleton instance.
    from app import state

    state.run_controller.current = RunProgress(active=True)

    resp = client.post("/api/jobs/run-now")
    assert resp.status_code == 200
    body = resp.json()
    assert body["started"] is False
    assert "already in progress" in body["reason"]

    state.run_controller.current = None  # cleanup


def test_clear_database_wipes_items_but_keeps_settings(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_clear.db")
    seed_conn = database.connect(db_path)
    database.apply_migrations(seed_conn)
    repository.upsert_item_seen(
        seed_conn, item_type="movie", bazarr_id=1, series_id=None,
        title="Fastball", series_title=None, season_episode=None,
        target_language="it",
    )
    repository.set_config(seed_conn, "source_lang_priority", ["en"])
    seed_conn.close()

    monkeypatch.setattr(settings, "db_path", db_path)
    monkeypatch.setattr(settings, "bazarr_base_url", "http://bazarr.test:6767")
    monkeypatch.setattr(settings, "bazarr_api_key", "testkey")

    with TestClient(app) as c:
        assert c.get("/api/queue").json()["total"] == 1

        resp = c.post("/api/jobs/clear-database")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cleared"] is True
        assert body["items_cleared"] == 1

        assert c.get("/api/queue").json()["total"] == 0

    # settings/app_config untouched — verified after the app shuts down its
    # own connection, from a fresh read of the same file.
    check_conn = database.connect(db_path)
    assert repository.get_config(check_conn, "source_lang_priority") == ["en"]
    check_conn.close()


def test_clear_database_refuses_while_a_run_is_active(client):
    from app import state
    from app.engine.runner import RunProgress

    state.run_controller.current = RunProgress(active=True)

    resp = client.post("/api/jobs/clear-database")
    assert resp.status_code == 409

    state.run_controller.current = None  # cleanup


def test_close_stale_runs_closes_open_runs(client):
    conn = database.connect(settings.db_path)
    stale_run = repository.start_run(conn, "manual_full")
    conn.close()

    resp = client.post("/api/jobs/close-stale-runs")
    assert resp.status_code == 200
    assert resp.json()["closed"] == 1

    check_conn = database.connect(settings.db_path)
    row = check_conn.execute(
        "SELECT finished_at FROM run_history WHERE id = ?", (stale_run,)
    ).fetchone()
    assert row["finished_at"] is not None
    check_conn.close()


def test_close_stale_runs_refuses_while_a_run_is_active(client):
    from app import state
    from app.engine.runner import RunProgress

    state.run_controller.current = RunProgress(active=True)

    resp = client.post("/api/jobs/close-stale-runs")
    assert resp.status_code == 409

    state.run_controller.current = None  # cleanup


def test_sync_media_starts_a_poll(client, monkeypatch):
    from app.engine.runner import RunController

    called = {"count": 0}

    async def fake_poll(self):
        called["count"] += 1
        return {}

    monkeypatch.setattr(RunController, "poll", fake_poll)

    resp = client.post("/api/jobs/sync-media")
    assert resp.status_code == 200
    assert resp.json()["started"] is True


def test_sync_media_refuses_when_a_run_is_active(client):
    from app import state
    from app.engine.runner import RunProgress

    state.run_controller.current = RunProgress(active=True)

    resp = client.post("/api/jobs/sync-media")
    body = resp.json()
    assert body["started"] is False
    assert "already in progress" in body["reason"]

    state.run_controller.current = None  # cleanup


def test_sync_subs_calls_warm_source_cache(client, monkeypatch):
    from app.engine.runner import RunController

    called = {"count": 0}

    async def fake_warm(self):
        called["count"] += 1
        return {"resolved": 2, "cached": 2}

    monkeypatch.setattr(RunController, "warm_source_cache", fake_warm)

    resp = client.post("/api/jobs/sync-subs")
    assert resp.status_code == 200
    assert resp.json()["started"] is True


def test_sync_subs_refuses_when_a_run_is_active(client):
    from app import state
    from app.engine.runner import RunProgress

    state.run_controller.current = RunProgress(active=True)

    resp = client.post("/api/jobs/sync-subs")
    body = resp.json()
    assert body["started"] is False
    assert "already in progress" in body["reason"]

    state.run_controller.current = None  # cleanup


def test_clear_engine_rate_limits_clears_flagged_instances(client):
    from app.db import engine_instances_repo

    conn = database.connect(settings.db_path)
    instance = engine_instances_repo.create_instance(
        conn, name="gemini", provider_type="gemini", config={"api_key": "x", "model": "m"}
    )
    for _ in range(engine_instances_repo.RATE_LIMIT_FAILURE_THRESHOLD):
        engine_instances_repo.record_rate_limited_failure(conn, instance["id"])
        conn.execute(
            "UPDATE engine_instances SET last_failure_at = NULL WHERE id = ?", (instance["id"],)
        )
        conn.commit()
    assert engine_instances_repo.get_instance(conn, instance["id"])["rate_limited_until"] is not None
    conn.close()

    resp = client.post("/api/jobs/clear-engine-rate-limits")
    assert resp.status_code == 200
    assert resp.json()["cleared"] == 1

    check_conn = database.connect(settings.db_path)
    assert engine_instances_repo.get_instance(check_conn, instance["id"])["rate_limited_until"] is None
    check_conn.close()


def test_clear_engine_rate_limits_reports_zero_when_nothing_flagged(client):
    resp = client.post("/api/jobs/clear-engine-rate-limits")
    assert resp.status_code == 200
    assert resp.json()["cleared"] == 0


def test_get_jobs_reports_sync_states(client):
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sync_media_active"] is False
    assert body["sync_subs_active"] is False
