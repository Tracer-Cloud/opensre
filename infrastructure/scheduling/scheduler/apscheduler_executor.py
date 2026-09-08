"""APScheduler executor that passes each scheduled fire time to the job."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from apscheduler.executors.base import run_job
from apscheduler.executors.pool import ThreadPoolExecutor


@dataclass(frozen=True)
class _ScheduledJobInvocation:
    """Expose one APScheduler run time through the job callable."""

    job: Any
    scheduled_run_time: datetime

    @property
    def id(self) -> str:
        return cast(str, self.job.id)

    @property
    def func(self) -> Any:
        def invoke(*args: Any, **kwargs: Any) -> Any:
            return self.job.func(
                *args,
                scheduled_run_time=self.scheduled_run_time,
                **kwargs,
            )

        return invoke

    @property
    def args(self) -> tuple[Any, ...]:
        return cast(tuple[Any, ...], self.job.args)

    @property
    def kwargs(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.job.kwargs)

    @property
    def misfire_grace_time(self) -> int | None:
        return cast(int | None, self.job.misfire_grace_time)


def _run_job_with_scheduled_time(
    job: Any,
    jobstore_alias: str,
    run_times: list[datetime],
    logger_name: str,
) -> list[Any]:
    """Run each submitted time with its own callback argument."""
    events: list[Any] = []
    for scheduled_run_time in run_times:
        invocation = _ScheduledJobInvocation(job, scheduled_run_time)
        events.extend(run_job(invocation, jobstore_alias, [scheduled_run_time], logger_name))
    return events


class ScheduledThreadPoolExecutor(ThreadPoolExecutor):
    """Run scheduler jobs with exact fire times attached to each callback."""

    def __init__(
        self,
        max_workers: int = 10,
        *,
        on_submit: Callable[[str, datetime], None] | None = None,
    ) -> None:
        self._on_submit = on_submit
        super().__init__(max_workers=max_workers)

    def _do_submit_job(self, job: Any, run_times: list[datetime]) -> None:
        for scheduled_run_time in run_times:
            if self._on_submit is not None:
                self._on_submit(job.id, scheduled_run_time)

        def callback(future: Future[list[Any]]) -> None:
            exc, traceback = (
                future.exception_info()
                if hasattr(future, "exception_info")
                else (future.exception(), getattr(future.exception(), "__traceback__", None))
            )
            if exc:
                self._run_job_error(job.id, exc, traceback)
            else:
                self._run_job_success(job.id, future.result())

        future = self._pool.submit(
            _run_job_with_scheduled_time,
            job,
            job._jobstore_alias,
            run_times,
            self._logger.name,
        )
        future.add_done_callback(callback)


__all__ = ["ScheduledThreadPoolExecutor"]
