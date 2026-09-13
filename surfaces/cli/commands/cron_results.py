"""CLI rendering of a retained scheduler attempt."""

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from infrastructure.scheduling.scheduler.types import TaskRun


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
        console.print(Markdown(run.report) if run.report else Text("Quiet run; no report body."))
    else:
        console.print(Text("Report not retained."))
    if run.error:
        console.print(Text(run.error))
