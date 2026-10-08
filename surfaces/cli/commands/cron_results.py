"""CLI rendering of a retained scheduler attempt."""

from rich.console import Console
from rich.text import Text

from infrastructure.scheduling.scheduler.types import TaskRun
from infrastructure.terminal.markdown import ReplyMarkdown


def print_run_result(console: Console, run: TaskRun) -> None:
    """Display work and delivery independently, followed by the retained report."""
    reason = f" — {run.work_error_kind}" if run.work_error_kind else ""
    console.print(Text(f"Run {run.run_id} · Work: {run.work_status.value}{reason}"))
    delivered = sum(target.ok for target in run.targets)
    delivery = (
        f"{delivered}/{len(run.targets)} delivered" if run.targets else "No delivery recorded"
    )
    console.print(Text(f"Delivery: {delivery}"))
    if run.report is not None:
        console.print(
            ReplyMarkdown(run.report) if run.report else Text("Quiet run; no report body.")
        )
    else:
        console.print(Text("Report not retained."))
    if run.error:
        console.print(Text(run.error))


def _run_status_label(run: TaskRun) -> str:
    """Describe whether a run was abandoned or recovered by a later attempt."""
    from infrastructure.scheduling.scheduler.types import TaskStatus

    if run.status is TaskStatus.ABANDONED:
        return "abandoned"
    if run.attempt > 1:
        return f"reclaimed/{run.status.value}"
    return run.status.value


def print_run_history(console: Console, runs: list[TaskRun]) -> None:
    """Show execution, work and delivery separately without squeezing diagnostic fields."""
    from infrastructure.terminal.theme import DIM, WARNING
    from surfaces.shared.terminal.components.time_format import format_repl_timestamp
    from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow, RecordTable

    rows: list[RecordRow] = []
    for run in runs:
        execution = _run_status_label(run)
        delivery = run.delivery_status.value if run.delivery_status is not None else "none"
        targets = (
            f"{sum(target.ok for target in run.targets)}/{len(run.targets)}" if run.targets else "—"
        )
        details = [Text(f"Attempt: {run.attempt} · Targets: {targets}", style=DIM)]
        if run.posted_message_id:
            details.append(Text(f"Message ID: {run.posted_message_id}", style=DIM))
        if run.work_error_kind:
            details.append(Text(f"Work detail: {run.work_error_kind}", style=WARNING))
        if run.error:
            details.append(Text(f"Error: {run.error}", style=WARNING))
        rows.append(
            RecordRow(
                (
                    Text(str(run.run_id or "—"), style="bold"),
                    Text(format_repl_timestamp(run.started_at, style="utc").removesuffix(" UTC")),
                    Text(execution),
                    Text(run.work_status.value),
                    Text(delivery),
                ),
                tuple(details),
            )
        )
    console.print(
        RecordTable(
            "Execution history",
            (
                RecordColumn("Run", 4),
                RecordColumn("Started", 19),
                RecordColumn("Execution", 18),
                RecordColumn("Work", 10),
                RecordColumn("Delivery", 8),
            ),
            tuple(rows),
            subtitle="Run times: UTC",
        )
    )
