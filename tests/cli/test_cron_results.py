"""A retained cron run report renders as reply Markdown."""

from __future__ import annotations

import io

from rich.console import Console

from infrastructure.scheduling.scheduler.types import TaskRun, TaskStatus
from surfaces.cli.commands.cron_results import RunNowOutput, print_run_result


def test_run_report_shows_link_urls() -> None:
    # Arrange: a retained report that links its pull request.
    url = "https://github.com/o/r/pull/1"
    run = TaskRun(task_id="t1", fire_time="2026-10-04T08:00", report=f"Opened [PR #1]({url}).")
    output = io.StringIO()
    console = Console(file=output, width=100, color_system=None)

    # Act
    print_run_result(console, run)

    # Assert: the report shows the URL, not only the link text.
    assert f"Opened PR #1 ({url})." in output.getvalue()


def test_a_failed_run_now_names_its_recorded_reason_not_the_logs() -> None:
    # Arrange: one run-now that recorded why it failed, one that recorded nothing.
    reason = "The workspace is out of OpenSRE credits. Top up or upgrade the plan."
    run = TaskRun(
        task_id="t1", fire_time="2026-11-11T05:00", status=TaskStatus.FAILED, error=reason
    )
    recorded, unrecorded = io.StringIO(), io.StringIO()
    shown = RunNowOutput(Console(file=recorded, width=200, color_system=None))
    silent = RunNowOutput(Console(file=unrecorded, width=200, color_system=None))

    # Act
    shown.show(run)
    shown.failed()
    silent.failed()

    # Assert: only a run with no recorded attempt still points at the logs.
    assert reason in recorded.getvalue()
    assert "Check logs" not in recorded.getvalue()
    assert "Task execution failed. Check logs for details." in unrecorded.getvalue()
