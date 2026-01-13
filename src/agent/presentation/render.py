"""
Rich/UI rendering functions.

All console output goes through here. Nodes stay pure.
"""

from rich.console import Console
from rich.panel import Panel

console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Investigation Start
# ─────────────────────────────────────────────────────────────────────────────

def render_investigation_start(alert_name: str, affected_table: str, severity: str):
    """Render the investigation header panel."""
    console.print(Panel(
        f"Investigation Started\n\n"
        f"Alert: {alert_name}\n"
        f"Table: {affected_table}\n"
        f"Severity: {severity}",
        title="Pipeline Investigation",
        border_style="cyan"
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Step Headers
# ─────────────────────────────────────────────────────────────────────────────

def render_step_header(step_num: int, title: str):
    """Render a step header."""
    console.print(f"\n[bold cyan]→ Step {step_num}: {title}[/]")


def render_api_response(label: str, data: str):
    """Render an API response line."""
    console.print(f"  [dim]API Response: {data}[/]")


def render_llm_thinking():
    """Render LLM thinking indicator."""
    console.print("  [dim]LLM interpreting...[/]")


def render_dot():
    """Render a streaming dot."""
    console.print("[dim].[/]", end="")


def render_newline():
    """Print a newline."""
    console.print()


# ─────────────────────────────────────────────────────────────────────────────
# Results
# ─────────────────────────────────────────────────────────────────────────────

def render_bullets(bullets: list[str], color: str = "green"):
    """Render bullet points."""
    for bullet in bullets:
        console.print(f"  [{color}]{bullet}[/]")


def render_root_cause_complete(bullets: list[str], confidence: float):
    """Render root cause completion."""
    console.print(f"  [green]✓[/] Root cause identified")
    for bullet in bullets:
        console.print(f"    {bullet}")
    console.print(f"  Confidence: [bold]{confidence:.0%}[/]")


def render_generating_outputs():
    """Render output generation step."""
    console.print("\n[bold cyan]→ Generating outputs...[/]")


# ─────────────────────────────────────────────────────────────────────────────
# Final Output
# ─────────────────────────────────────────────────────────────────────────────

def render_agent_output(slack_message: str):
    """Render the agent output panel."""
    console.print("\n")
    console.print(Panel(slack_message, title="Agent Output", border_style="blue"))


def render_saved_file(path: str):
    """Render a saved file message."""
    console.print(f"[green]✓[/] Saved: {path}")

