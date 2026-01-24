"""Rich/UI rendering functions.

NOTE: Most rendering now happens via src.agent.output module.
This file is kept for backward compatibility.
"""

from rich.console import Console
from rich.panel import Panel

console = Console()


def render_final_report(slack_message: str):
    """Render the final RCA report panel."""
    console.print("\n")
    console.print(
        Panel(
            slack_message,
            title="RCA Report",
            border_style="green",
        )
    )
