"""Terminal rendering for RCA reports."""

from rich.console import Console
from rich.panel import Panel

from app.agent.output import get_output_format


def render_report(slack_message: str) -> None:
    """Render the final report to terminal.

    Uses Rich for formatted output when available, falls back to plain text.

    Args:
        slack_message: Formatted report message
    """
    fmt = get_output_format()

    if not slack_message:
        _render_empty_report(fmt)
        return

    if fmt == "rich":
        _render_rich_report(slack_message)
    else:
        _render_plain_report(slack_message)


def _render_empty_report(fmt: str) -> None:
    """Render message when no report is generated."""
    if fmt == "rich":
        Console().print("[yellow]No report generated.[/]")
    else:
        print("No report generated.")


def _render_rich_report(slack_message: str) -> None:
    """Render report using Rich formatting."""
    console = Console()
    console.print()
    console.print(Panel(slack_message, title="RCA Report", border_style="green"))
    console.print("\nInvestigation complete.")


def _render_plain_report(slack_message: str) -> None:
    """Render report using plain text formatting."""
    print("\n" + "=" * 60)
    print("RCA REPORT")
    print("=" * 60)
    print(slack_message)
    print("=" * 60)
    print("Investigation complete.")
