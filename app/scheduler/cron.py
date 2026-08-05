import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# Default/primary job kept under its original id so the existing single-job
# call sites (install(cron_expr, callback) with no job_id) keep working
# unchanged.
_DEFAULT_JOB_ID = "scheduled_run"


class CronScheduler:
    """Wraps APScheduler to drive scheduled jobs (the main translation run,
    plus any number of independently-configured jobs like the Bazarr sync
    jobs) each on its own user-configurable cron expression. max_instances=1
    prevents overlapping runs of the same job if one take longer than the
    interval between fires."""

    def __init__(self):
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self._scheduler.start()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    def install(self, cron_expr: str, callback, job_id: str = _DEFAULT_JOB_ID) -> None:
        trigger = CronTrigger.from_crontab(cron_expr)
        self._scheduler.add_job(
            callback,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
        logger.info("Scheduled job %r installed with cron expression: %s", job_id, cron_expr)

    def reschedule(self, cron_expr: str, job_id: str = _DEFAULT_JOB_ID) -> None:
        """Reinstalls a job with a new cron expression — callable live from
        the Settings API without restarting the app."""
        job = self._scheduler.get_job(job_id)
        if job is None:
            raise RuntimeError(f"No job {job_id!r} installed yet; call install() first")
        callback = job.func
        self.install(cron_expr, callback, job_id=job_id)

    def remove(self, job_id: str) -> None:
        """No-op if the job isn't installed — lets callers unconditionally
        clear a job when its cron expression is set to empty (disabled)."""
        if self._scheduler.get_job(job_id) is not None:
            self._scheduler.remove_job(job_id)
            logger.info("Scheduled job %r removed", job_id)

    def next_run_time(self, job_id: str = _DEFAULT_JOB_ID):
        job = self._scheduler.get_job(job_id)
        return job.next_run_time if job else None
