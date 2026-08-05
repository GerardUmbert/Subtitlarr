import pytest

from app.scheduler.cron import CronScheduler


@pytest.mark.asyncio
async def test_install_and_next_run_time_computed():
    scheduler = CronScheduler()
    scheduler.start()
    try:
        calls = []
        scheduler.install("0 3 * * *", lambda: calls.append(1))
        next_run = scheduler.next_run_time()
        assert next_run is not None
        assert next_run.hour == 3
        assert next_run.minute == 0
    finally:
        scheduler.shutdown()


@pytest.mark.asyncio
async def test_reschedule_changes_next_run_time():
    scheduler = CronScheduler()
    scheduler.start()
    try:
        scheduler.install("0 3 * * *", lambda: None)
        scheduler.reschedule("30 5 * * *")
        next_run = scheduler.next_run_time()
        assert next_run.hour == 5
        assert next_run.minute == 30
    finally:
        scheduler.shutdown()


@pytest.mark.asyncio
async def test_reschedule_without_install_raises():
    scheduler = CronScheduler()
    scheduler.start()
    try:
        with pytest.raises(RuntimeError):
            scheduler.reschedule("0 3 * * *")
    finally:
        scheduler.shutdown()


@pytest.mark.asyncio
async def test_invalid_cron_expression_raises():
    scheduler = CronScheduler()
    scheduler.start()
    try:
        with pytest.raises(ValueError):
            scheduler.install("not a cron expr", lambda: None)
    finally:
        scheduler.shutdown()


@pytest.mark.asyncio
async def test_multiple_named_jobs_are_independent():
    """The default translation job and the two Bazarr sync jobs must not
    collide or overwrite each other despite sharing one scheduler instance."""
    scheduler = CronScheduler()
    scheduler.start()
    try:
        scheduler.install("0 3 * * *", lambda: None)  # default job_id
        scheduler.install("40 9 * * *", lambda: None, job_id="sync_media")
        scheduler.install("40 9 * * *", lambda: None, job_id="sync_subs")

        assert scheduler.next_run_time().hour == 3
        assert scheduler.next_run_time("sync_media").hour == 9
        assert scheduler.next_run_time("sync_media").minute == 40
        assert scheduler.next_run_time("sync_subs").hour == 9

        scheduler.reschedule("15 11 * * *", job_id="sync_media")
        assert scheduler.next_run_time("sync_media").hour == 11
        assert scheduler.next_run_time("sync_subs").hour == 9  # unaffected
    finally:
        scheduler.shutdown()


@pytest.mark.asyncio
async def test_remove_uninstalls_a_job():
    scheduler = CronScheduler()
    scheduler.start()
    try:
        scheduler.install("40 9 * * *", lambda: None, job_id="sync_media")
        assert scheduler.next_run_time("sync_media") is not None

        scheduler.remove("sync_media")
        assert scheduler.next_run_time("sync_media") is None
    finally:
        scheduler.shutdown()


@pytest.mark.asyncio
async def test_remove_on_a_job_that_was_never_installed_is_a_noop():
    scheduler = CronScheduler()
    scheduler.start()
    try:
        scheduler.remove("never_installed")  # must not raise
    finally:
        scheduler.shutdown()
