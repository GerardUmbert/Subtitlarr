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
