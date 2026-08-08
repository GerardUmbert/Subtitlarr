import pytest

from app import state as app_state
from app.api import jobs
from app.db import database


class FakeProgress:
    def __init__(self, active: bool):
        self.active = active


class FakeRunner:
    def __init__(self, run_active: bool = False):
        self.current = FakeProgress(run_active) if run_active else None
        self.polled = 0
        self.warmed = 0

    async def poll(self):
        self.polled += 1
        return {"episodes_seen": 0, "movies_seen": 0}

    async def warm_source_cache(self):
        self.warmed += 1
        return {"resolved": 0, "cached": 0}


@pytest.fixture(autouse=True)
def reset_state(tmp_path):
    """These module-level dicts are shared mutable state across tests —
    reset before and after each test so one test's sync doesn't leave
    'active' stuck true for the next. Also wires up a real (temp, on-disk)
    DB connection on app.state, since _run_sync_media/_run_sync_subs now
    log start/finish via repository.start_job_event/finish_job_event,
    which need a live connection rather than the app's normal
    lifespan-managed one (not running under these direct-call unit
    tests)."""
    conn = database.connect(str(tmp_path / "test.db"))
    database.apply_migrations(conn)
    app_state.db_conn = conn
    for state in (jobs._sync_media_state, jobs._sync_subs_state):
        state["active"] = False
        state["error"] = None
    yield
    for state in (jobs._sync_media_state, jobs._sync_subs_state):
        state["active"] = False
        state["error"] = None
    conn.close()
    app_state.db_conn = None


@pytest.mark.asyncio
async def test_cron_sync_media_runs_when_idle():
    runner = FakeRunner()
    await jobs.cron_sync_media(runner)
    assert runner.polled == 1
    assert jobs._sync_media_state["active"] is False  # finished, reset

    events = app_state.get_conn().execute("SELECT * FROM job_events").fetchall()
    assert len(events) == 1
    assert events[0]["job"] == "sync_media"
    assert events[0]["triggered_by"] == "cron"
    assert events[0]["status"] == "done"


@pytest.mark.asyncio
async def test_cron_sync_media_skips_when_a_translation_run_is_active():
    runner = FakeRunner(run_active=True)
    await jobs.cron_sync_media(runner)
    assert runner.polled == 0


@pytest.mark.asyncio
async def test_cron_sync_media_skips_when_already_syncing():
    runner = FakeRunner()
    jobs._sync_media_state["active"] = True
    await jobs.cron_sync_media(runner)
    assert runner.polled == 0


@pytest.mark.asyncio
async def test_cron_sync_subs_runs_when_idle():
    runner = FakeRunner()
    await jobs.cron_sync_subs(runner)
    assert runner.warmed == 1
    assert jobs._sync_subs_state["active"] is False

    events = app_state.get_conn().execute("SELECT * FROM job_events").fetchall()
    assert len(events) == 1
    assert events[0]["job"] == "sync_subs"
    assert events[0]["triggered_by"] == "cron"
    assert events[0]["status"] == "done"


@pytest.mark.asyncio
async def test_cron_sync_subs_skips_when_a_translation_run_is_active():
    runner = FakeRunner(run_active=True)
    await jobs.cron_sync_subs(runner)
    assert runner.warmed == 0
