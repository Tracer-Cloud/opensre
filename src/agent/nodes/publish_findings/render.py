"""Rich/UI rendering functions."""

from rich.console import Console
from rich.panel import Panel

console = Console()

# Map plan sources to human-readable names
SOURCE_NAMES = {
    "tracer": "Tracer Pipeline Status",
    "storage": "S3 Storage Check",
    "batch": "AWS Batch Jobs",
}


def render_incoming_alert(raw_alert_text: str):
    """Render the incoming Grafana alert payload."""
    console.print("\n")
    console.print(Panel(
        raw_alert_text,
        title="Incoming Grafana Alert (Slack Channel)",
        border_style="red",
    ))
    console.print("[dim]Agent triggered automatically...[/dim]\n")


def render_plan(plan_sources: list[str]):
    """Render the investigation plan (hypotheses to check)."""
    console.print("\n[bold magenta]─── Investigation Plan ───[/]")
    console.print("[bold]Evidence sources to check:[/]\n")
    for i, source in enumerate(plan_sources, 1):
        name = SOURCE_NAMES.get(source, source)
        console.print(f"  [cyan]H{i}[/] [bold]{name}[/]")
    console.print()


def render_evidence(evidence: dict):
    """Render collected evidence."""
    console.print("\n[bold yellow]─── Evidence Collection ───[/]")

    # S3 evidence
    if "s3" in evidence:
        s3 = evidence["s3"]
        console.print("\n[bold cyan]→ S3 Storage Check[/]")
        if s3.get("error"):
            console.print(f"  [red]Error: {s3['error']}[/]")
        else:
            marker = "[green]✓ Found[/]" if s3.get("marker_exists") else "[red]✗ Missing[/]"
            console.print(f"  [dim]_SUCCESS marker:[/] {marker}")
            console.print(f"  [dim]Files found:[/] {s3.get('file_count', 0)}")
            if s3.get("files"):
                for f in s3["files"][:3]:
                    console.print(f"    [dim]- {f}[/]")

    # Pipeline run evidence
    if "pipeline_run" in evidence:
        run = evidence["pipeline_run"]
        console.print("\n[bold cyan]→ Tracer Pipeline Status[/]")
        if not run.get("found"):
            console.print("  [yellow]No recent pipeline runs found[/]")
        else:
            status = run.get("status", "Unknown")
            status_color = "red bold" if status.lower() == "failed" else "green"
            console.print(f"  [dim]Pipeline:[/] {run.get('pipeline_name', 'Unknown')}")
            console.print(f"  [dim]Run:[/] {run.get('run_name', 'Unknown')}")
            console.print(f"  [dim]Status:[/] [{status_color}]{status}[/]")
            console.print(f"  [dim]Duration:[/] {run.get('run_time_minutes', 0)} min")
            console.print(f"  [dim]Cost:[/] [yellow]${run.get('run_cost_usd', 0):.2f}[/]")
            console.print(f"  [dim]User:[/] {run.get('user_email', 'Unknown')}")

    # Batch jobs evidence
    if "batch_jobs" in evidence:
        batch = evidence["batch_jobs"]
        console.print("\n[bold cyan]→ AWS Batch Jobs[/]")
        if not batch.get("found"):
            console.print("  [yellow]No AWS Batch jobs found[/]")
        else:
            console.print(f"  [dim]Total jobs:[/] {batch.get('total_jobs', 0)}")
            console.print(f"  [dim]Succeeded:[/] [green]{batch.get('succeeded_jobs', 0)}[/]")
            failed = batch.get("failed_jobs", 0)
            if failed > 0:
                console.print(f"  [dim]Failed:[/] [red bold]{failed}[/]")
                if batch.get("failure_reason"):
                    console.print(f"  [red bold]Failure reason:[/] [red]{batch['failure_reason']}[/]")


def render_analysis(root_cause: str, confidence: float):
    """Render the root cause analysis."""
    console.print("\n[bold green]─── Root Cause Analysis ───[/]")

    # Parse bullet points from root_cause
    bullets = [line.strip().lstrip("*- ") for line in root_cause.split("\n") if line.strip()]
    render_root_cause_complete(bullets, confidence)


def render_final_report(slack_message: str):
    """Render the final RCA report panel."""
    console.print("\n")
    console.print(Panel(
        slack_message,
        title="RCA Report",
        border_style="green",
    ))


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

