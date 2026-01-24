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
    severity_color = "red" if severity == "critical" else "yellow"
    console.print(Panel(
        f"Investigation Started\n\n"
        f"Alert: [bold]{alert_name}[/]\n"
        f"Table: [cyan]{affected_table}[/]\n"
        f"Severity: [{severity_color}]{severity}[/]",
        title="Pipeline Investigation",
        border_style="cyan"
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Step Headers
# ─────────────────────────────────────────────────────────────────────────────

def render_step_header(step_num: int, title: str):
    """Render a step header."""
    console.print(f"\n[bold cyan]→ Step {step_num}: {title}[/]")


def render_api_response(label: str, data: str, is_error: bool = False):
    """Render an API response line with color coding."""
    if is_error:
        console.print(f"  [red bold]API Response ({label}): {data}[/]")
    else:
        console.print(f"  [dim]API Response ({label}): {data}[/]")


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

def render_bullets(bullets: list[str], is_error: bool = False):
    """Render bullet points with appropriate color."""
    color = "red" if is_error else "yellow"
    for bullet in bullets:
        # Check if bullet contains error keywords
        if any(word in bullet.lower() for word in ["fail", "error", "killed", "oom", "denied", "missing"]):
            console.print(f"  [red]{bullet}[/]")
        else:
            console.print(f"  [{color}]{bullet}[/]")


def render_root_cause_complete(bullets: list[str], confidence: float):
    """Render root cause completion."""
    console.print("  [green bold][ROOT CAUSE IDENTIFIED][/]")
    for bullet in bullets:
        # Color code based on content
        if any(word in bullet.lower() for word in ["fail", "error", "killed", "oom", "denied"]):
            console.print(f"    [red]{bullet}[/]")
        else:
            console.print(f"    [white]{bullet}[/]")
    console.print(f"  Confidence: [bold cyan]{confidence:.0%}[/]")


def render_generating_outputs():
    """Render output generation step."""
    console.print("\n[bold cyan]→ Generating outputs...[/]")


# ─────────────────────────────────────────────────────────────────────────────
# Final Output
# ─────────────────────────────────────────────────────────────────────────────

def render_agent_output(slack_message: str):
    """Render the agent output panel with styled link."""
    console.print("\n")

    # Style the Tracer link in cyan/blue for visibility
    import re
    tracer_url_pattern = r'(https://staging\.tracer\.cloud/[^\s]+)'

    def style_url(match):
        url = match.group(1)
        return f"[bold cyan underline]{url}[/bold cyan underline]"

    styled_message = re.sub(tracer_url_pattern, style_url, slack_message)

    from rich.text import Text
    text = Text.from_markup(styled_message)
    console.print(Panel(text, title="RCA Report", border_style="blue"))


def render_saved_file(path: str):
    """Render a saved file message."""
    console.print(f"[green][OK][/] Saved: {path}")

