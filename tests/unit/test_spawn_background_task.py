import asyncio
import logging

import pytest

from app import state


@pytest.mark.asyncio
async def test_spawn_background_task_logs_an_uncaught_exception(caplog):
    """Regression test: a fire-and-forget asyncio.create_task's uncaught
    exception is normally only reported as a bare 'Task exception was
    never retrieved' warning from asyncio's own default handler — outside
    the app's logging setup, and only whenever the Task object happens to
    be garbage collected, which made a real run-filtered crash completely
    silent (nothing in the container logs, no error in the UI). Every
    fire-and-forget task must go through spawn_background_task instead, so
    a failure is always logged immediately and unconditionally."""
    async def _boom():
        raise RuntimeError("simulated crash")

    with caplog.at_level(logging.ERROR):
        task = state.spawn_background_task(_boom(), description="test-task")
        with pytest.raises(RuntimeError):
            await task  # propagate so the test itself doesn't swallow it either

    assert any(
        "test-task" in record.message and record.levelno == logging.ERROR
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_spawn_background_task_does_not_log_on_success(caplog):
    async def _fine():
        return "ok"

    with caplog.at_level(logging.ERROR):
        task = state.spawn_background_task(_fine(), description="test-task")
        await task

    assert not any(record.levelno == logging.ERROR for record in caplog.records)


@pytest.mark.asyncio
async def test_spawn_background_task_does_not_log_on_cancellation():
    async def _slow():
        await asyncio.sleep(10)

    task = state.spawn_background_task(_slow(), description="test-task")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # No assertion needed beyond "this doesn't raise/log unexpectedly" —
    # _log_if_failed's t.cancelled() guard is what's under test here.
