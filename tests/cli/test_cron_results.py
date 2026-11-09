"""A retained cron run report renders as reply Markdown."""

from __future__ import annotations

import io

from rich.console import Console

from infrastructure.scheduling.scheduler.types import TaskRun
from surfaces.cli.commands.cron_results import print_run_result


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
