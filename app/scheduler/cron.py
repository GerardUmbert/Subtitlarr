import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_JOB_ID = "scheduled_run"


class CronScheduler:
    """Wraps APScheduler to drive the scheduled translation run on a
    user-configurable cron expression. max_instances=1 prevents overlapping
    runs if a batch takes longer than the interval between fires."""

    def __init__(self):
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self._scheduler.start()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    def install(self, cron_expr: str, callback) -> None:
        trigger = CronTrigger.from_crontab(cron_expr)
        self._scheduler.add_job(
            callback,
            trigger=trigger,
            id=_JOB_ID,
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
        logger.info("Scheduled job installed with cron expression: %s", cron_expr)

    def reschedule(self, cron_expr: str) -> None:
        """Reinstalls the job with a new cron expression — callable live from
        the Settings API without restarting the app."""
        job = self._scheduler.get_job(_JOB_ID)
        if job is None:
            raise RuntimeError("No scheduled job installed yet; call install() first")
        callback = job.func
        self.install(cron_expr, callback)

    def next_run_time(self):
        job = self._scheduler.get_job(_JOB_ID)
        return job.next_run_time if job else None
