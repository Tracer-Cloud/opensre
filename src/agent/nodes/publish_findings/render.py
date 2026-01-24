"""Rich/UI rendering functions."""

from rich.console import Console
from rich.panel import Panel

console = Console()

# Map plan sources to human-readable names
SOURCE_NAMES = {
    "tracer": "Tracer Pipeline Status",
    "storage": "S3 Storage Check",
    "batch": "AWS Batch Jobs",
    "tracer_web": "Tracer Web App Runs",
}


def render_incoming_alert(alert_text: str):
    """Render the incoming Grafana alert payload."""
    console.print("\n")
    console.print(Panel(
        alert_text,
        title="Incoming Grafana Alert (Slack Channel)",
        border_style="red",
    ))
    console.print("[dim]Agent triggered automatically...[/dim]\n")


def _format_rationale(rationale: str) -> str:
    """Format rationale text as bullet points."""
    import re

    # Try to detect numbered items (e.g., "1) ... 2) ..." or "1. ... 2. ...")
    # Split by numbered patterns to extract each item
    numbered_pattern = r"(\d+)[.)]\s+"
    parts = re.split(numbered_pattern, rationale)

    if len(parts) > 3:  # Found numbered items (odd indices are numbers, even are text)
        bullets = []
        # Skip first part (text before first number), then process pairs
        for i in range(1, len(parts), 2):
            if i + 1 < len(parts):
                text = parts[i + 1].strip()
                # Remove trailing whitespace and clean up
                text = re.sub(r"\s+", " ", text).strip()
                # Stop at next numbered item if present in the text
                next_num_match = re.search(r"\s+\d+[.)]\s+", text)
                if next_num_match:
                    text = text[: next_num_match.start()].strip()
                # Remove trailing punctuation
                text = re.sub(r"\.\s*$", "", text)
                if text:
                    bullets.append(f"  • {text}")
        if bullets:
            return "\n".join(bullets)

    # If no numbered items, try to split by sentences and format as bullets
    sentences = re.split(r"([.!?])\s+", rationale)
    if len(sentences) > 3:  # Multiple sentences
        bullets = []
        current_sentence = ""
        for i, part in enumerate(sentences):
            current_sentence += part
            if part in ".!?" and i < len(sentences) - 1:
                bullet = current_sentence.strip()
                if bullet:
                    bullets.append(f"  • {bullet}")
                current_sentence = ""
        if current_sentence.strip():
            bullets.append(f"  • {current_sentence.strip()}")
        if bullets:
            return "\n".join(bullets)

    # Fallback: return as single bullet
    return f"  • {rationale.strip()}"


def render_plan(plan_sources: list[str], rationale: str | None = None):
    """Render the investigation plan (hypotheses to check)."""
    console.print("\n[bold magenta]─── Investigation Plan ───[/]")
    console.print("[bold]Evidence sources to check:[/]\n")
    for i, source in enumerate(plan_sources, 1):
        name = SOURCE_NAMES.get(source, source)
        console.print(f"  [cyan]H{i}[/] [bold]{name}[/]")
    console.print()
    if rationale:
        formatted_rationale = _format_rationale(rationale)
        console.print("[dim]Rationale:[/]")
        console.print(formatted_rationale)
        console.print()


def render_evidence(evidence: dict):
    """Render collected evidence."""
    console.print("\n[bold yellow]─── Evidence Collection ───[/]")

    # S3 evidence
    if "s3" in evidence:
        s3 = evidence["s3"]
        console.print("\n[bold cyan]→ S3 Storage Check[/]")
        if s3.get("error"):
            console.print(f"  [yellow]{s3['error']}[/]")
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

    # Tracer web app evidence
    if "tracer_web_run" in evidence:
        web_run = evidence["tracer_web_run"]
        console.print("\n[bold cyan]→ Tracer Web App Runs[/]")
        if not web_run.get("found"):
            console.print("  [yellow]No failed runs found via web app[/]")
        else:
            status = web_run.get("status", "Unknown")
            status_color = "red bold" if str(status).lower() == "failed" else "green"
            console.print(f"  [dim]Pipeline:[/] {web_run.get('pipeline_name', 'Unknown')}")
            console.print(f"  [dim]Run:[/] {web_run.get('run_name', 'Unknown')}")
            console.print(f"  [dim]Status:[/] [{status_color}]{status}[/]")
            console.print(f"  [dim]Trace:[/] {web_run.get('trace_id', 'Unknown')}")
            console.print(f"  [dim]Cost:[/] [yellow]${web_run.get('run_cost', 0):.2f}[/]")
            console.print(f"  [dim]User:[/] {web_run.get('user_email', 'Unknown')}")

            # Show run URL
            run_url = web_run.get("run_url")
            if run_url:
                console.print(f"  [dim]View run:[/] [blue underline]{run_url}[/]")

            # Show failed jobs, tools, and logs
            failed_jobs = web_run.get("failed_jobs", [])
            if failed_jobs:
                console.print(f"  [dim]Failed jobs:[/] [red bold]{len(failed_jobs)}[/]")
                for job in failed_jobs[:2]:
                    console.print(f"    [red]- {job.get('job_name', 'Unknown')}: {job.get('status_reason', '')}[/]")
                    if job.get("exit_code"):
                        console.print(f"      [dim]Exit code: {job.get('exit_code')}[/]")

            failed_tools = web_run.get("failed_tools", [])
            if failed_tools:
                console.print(f"  [dim]Failed tools:[/] [red bold]{len(failed_tools)}[/]")
                for tool in failed_tools[:2]:
                    console.print(f"    [red]- {tool.get('tool_name', 'Unknown')} (exit_code={tool.get('exit_code')})[/]")

            total_logs = web_run.get("total_logs", 0)
            error_logs = web_run.get("error_logs", [])
            if total_logs > 0:
                console.print(f"  [dim]Total logs:[/] {total_logs}")
                if error_logs:
                    console.print(f"  [dim]Error logs:[/] [red bold]{len(error_logs)}[/]")
                    # Show first few error log messages
                    for log in error_logs[:3]:
                        level = log.get("log_level", "UNKNOWN")
                        message = log.get("message", "")[:200]
                        console.print(f"    [red][{level}][/] {message}")
            else:
                console.print("  [dim]Logs:[/] [yellow]No logs available[/]")

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
    """Render the root cause analysis in a human-readable format."""
    console.print("\n[bold green]─── Root Cause Analysis ───[/]\n")

    # Check if this is an error message
    if "ERROR:" in root_cause or "No root cause has been identified" in root_cause:
        console.print(f"[bold red]{root_cause}[/]")
        console.print(f"\n[dim]Confidence:[/] [red]{confidence:.0%}[/]")
        return

    # Parse the root cause text
    lines = root_cause.split("\n")
    in_root_cause_section = False
    bullets = []
    causal_chain = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Check for ROOT_CAUSE: header
        if line.upper().startswith("ROOT_CAUSE"):
            in_root_cause_section = True
            continue

        # Check for CONFIDENCE: header
        if line.upper().startswith("CONFIDENCE"):
            in_root_cause_section = False
            continue

        # Remove bullet markers
        clean_line = line.lstrip("*-• ").strip()
        if not clean_line:
            continue

        if in_root_cause_section:
            bullets.append(clean_line)
        else:
            causal_chain.append(clean_line)

    # Render root cause findings
    if bullets:
        console.print("[bold cyan]Root Cause Findings:[/]")
        for i, bullet in enumerate(bullets, 1):
            # Color code based on content
            if any(word in bullet.lower() for word in ["fail", "error", "killed", "oom", "denied", "missing", "timeout"]):
                console.print(f"  {i}. [red]•[/] [red]{bullet}[/]")
            elif any(word in bullet.lower() for word in ["success", "working", "passed", "completed"]):
                console.print(f"  {i}. [green]•[/] [green]{bullet}[/]")
            else:
                console.print(f"  {i}. [yellow]•[/] {bullet}")
        console.print()

    # Render causal chain if available
    if causal_chain:
        console.print("[bold cyan]Causal Chain:[/]")
        for i, chain_item in enumerate(causal_chain, 1):
            console.print(f"  {i}. {chain_item}")
        console.print()

    # Render confidence
    confidence_color = "green" if confidence >= 0.7 else "yellow" if confidence >= 0.4 else "red"
    console.print(f"[bold]Confidence:[/] [{confidence_color}]{confidence:.0%}[/]")
    if confidence >= 0.8:
        console.print("[dim]  (High confidence - strong evidence)[/]")
    elif confidence >= 0.5:
        console.print("[dim]  (Moderate confidence - some evidence)[/]")
    else:
        console.print("[dim]  (Low confidence - limited evidence)[/]")


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


# ─────────────────────────────────────────────────────────────────────────────
# Results
# ─────────────────────────────────────────────────────────────────────────────

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

