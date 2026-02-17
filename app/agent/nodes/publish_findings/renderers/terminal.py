"""Terminal rendering for RCA reports."""

from rich.console import Console
from rich.panel import Panel

from app.agent.output import get_output_format


def render_report(slack_message: str, confidence: float) -> None:
    """Render the final report to terminal.

    Uses Rich for formatted output when available, falls back to plain text.

    Args:
        slack_message: Formatted report message
        confidence: Confidence score (0.0 to 1.0)

    Returns:
        None - outputs directly to terminal
    """
    fmt = get_output_format()

    if not slack_message:
        _render_empty_report(fmt)
        return

    if fmt == "rich":
        _render_rich_report(slack_message, confidence)
    else:
        _render_plain_report(slack_message, confidence)


def _render_empty_report(fmt: str) -> None:
    """Render message when no report is generated."""
    if fmt == "rich":
        Console().print("[yellow]No report generated.[/]")
    else:
        print("No report generated.")


def _render_rich_report(slack_message: str, confidence: float) -> None:
    """Render report using Rich formatting.

    Args:
        slack_message: Formatted report message
        confidence: Confidence score
    """
    console = Console()
    console.print()
    console.print(Panel(slack_message, title="RCA Report", border_style="green"))
    console.print(f"\nInvestigation complete. Confidence: {confidence:.0%}")


def _render_plain_report(slack_message: str, confidence: float) -> None:
    """Render report using plain text formatting.

    Args:
        slack_message: Formatted report message
        confidence: Confidence score
    """
    print("\n" + "=" * 60)
    print("RCA REPORT")
    print("=" * 60)
    print(slack_message)
    print("=" * 60)
    print(f"Investigation complete. Confidence: {confidence:.0%}")
