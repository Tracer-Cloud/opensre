"""
Rich/UI rendering functions.

All console output goes through here. Nodes stay pure.
"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

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


def render_tracer_run_details(
    pipeline_name: str,
    run_name: str,
    status: str,
    user_email: str,
    team: str,
    run_cost: float,
    runtime_seconds: float,
    instance_type: str,
    max_ram_gb: float,
):
    """Render detailed Tracer run information in a table."""
    status_color = "red bold" if status.lower() == "failed" else "green"
    
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim")
    table.add_column("Value")
    
    table.add_row("Pipeline", f"[cyan]{pipeline_name}[/]")
    table.add_row("Run Name", run_name)
    table.add_row("Status", f"[{status_color}]{status}[/]")
    table.add_row("User", user_email)
    table.add_row("Team", team)
    table.add_row("Cost", f"[yellow]${run_cost:.2f}[/]")
    table.add_row("Runtime", f"{runtime_seconds/60:.1f} min")
    table.add_row("Instance", instance_type)
    table.add_row("Max RAM", f"{max_ram_gb:.1f} GB")
    
    console.print(table)


def render_batch_job_details(
    job_name: str,
    status: str,
    failure_reason: str | None,
    exit_code: int | None,
    vcpu: int,
    memory_gb: float,
    gpu_count: int,
):
    """Render AWS Batch job details."""
    status_color = "red bold" if status == "FAILED" else "green"
    
    console.print(f"  [dim]Job:[/] {job_name}")
    console.print(f"  [dim]Status:[/] [{status_color}]{status}[/]")
    if failure_reason:
        console.print(f"  [red bold]Failure:[/] [red]{failure_reason}[/]")
    if exit_code is not None:
        console.print(f"  [dim]Exit Code:[/] [{'red' if exit_code != 0 else 'green'}]{exit_code}[/]")
    console.print(f"  [dim]Resources:[/] {vcpu} vCPU, {memory_gb:.0f} GB RAM, {gpu_count} GPU")


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
    console.print(f"  [green bold][ROOT CAUSE IDENTIFIED][/]")
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
    """Render the agent output panel."""
    console.print("\n")
    console.print(Panel(slack_message, title="Agent Output", border_style="blue"))


def render_saved_file(path: str):
    """Render a saved file message."""
    console.print(f"[green][OK][/] Saved: {path}")


def render_error(message: str):
    """Render an error message."""
    console.print(f"[red bold][ERROR][/] {message}")

