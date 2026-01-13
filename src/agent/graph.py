"""
Investigation Graph - LangGraph state machine for incident resolution.

The graph is explicit:
    START → check_s3 → check_nextflow → determine_root_cause → output → END

Two external context calls:
    1. S3: Check if _SUCCESS marker exists
    2. Nextflow: Get finalize step status + logs
"""

from typing import TypedDict, Literal
from langgraph.graph import StateGraph, START, END
from rich.console import Console
from rich.panel import Panel

from src.mocks.s3 import get_s3_client
from src.mocks.nextflow import get_nextflow_client

console = Console()

# ─────────────────────────────────────────────────────────────────────────────
# STATE - Everything the graph needs to know
# ─────────────────────────────────────────────────────────────────────────────

class InvestigationState(TypedDict):
    # Input
    alert_name: str
    affected_table: str
    severity: str
    
    # Evidence (from tools)
    s3_marker_exists: bool | None
    s3_file_count: int
    nextflow_finalize_status: str | None
    nextflow_logs: str | None
    
    # Output
    root_cause: str | None
    confidence: float
    slack_message: str | None
    problem_md: str | None


# ─────────────────────────────────────────────────────────────────────────────
# TOOLS - Plain functions, no classes
# ─────────────────────────────────────────────────────────────────────────────

def check_s3_marker(bucket: str, prefix: str) -> dict:
    """Check if _SUCCESS marker exists in S3. Returns marker status + file count."""
    s3 = get_s3_client()
    files = s3.list_objects(bucket, prefix)
    marker_exists = s3.object_exists(bucket, f"{prefix}_SUCCESS")
    return {
        "marker_exists": marker_exists,
        "file_count": len(files),
        "files": [f["key"] for f in files],
    }


def check_nextflow_finalize(pipeline_id: str) -> dict:
    """Get Nextflow finalize step status and logs."""
    nf = get_nextflow_client()
    run = nf.get_latest_run(pipeline_id)
    if not run:
        return {"found": False, "status": None, "logs": None}
    
    steps = nf.get_steps(run["run_id"])
    finalize = next((s for s in steps if s["step_name"] == "finalize"), None)
    logs = nf.get_step_logs(run["run_id"], "finalize") if finalize else None
    
    return {
        "found": True,
        "status": finalize["status"] if finalize else None,
        "error": finalize.get("error") if finalize else None,
        "logs": logs,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GRAPH NODES - Each transforms state
# ─────────────────────────────────────────────────────────────────────────────

def node_check_s3(state: InvestigationState) -> dict:
    """Node 1: Check S3 for _SUCCESS marker."""
    console.print("\n[bold cyan]→ Checking S3 for _SUCCESS marker...[/]")
    
    result = check_s3_marker("tracer-processed-data", "events/2026-01-13/")
    
    status = "✓ EXISTS" if result["marker_exists"] else "✗ MISSING"
    console.print(f"  _SUCCESS marker: [{'green' if result['marker_exists'] else 'red'}]{status}[/]")
    console.print(f"  Files in prefix: {result['file_count']}")
    
    return {
        "s3_marker_exists": result["marker_exists"],
        "s3_file_count": result["file_count"],
    }


def node_check_nextflow(state: InvestigationState) -> dict:
    """Node 2: Check Nextflow finalize step."""
    console.print("\n[bold cyan]→ Checking Nextflow finalize step...[/]")
    
    result = check_nextflow_finalize("events-etl")
    
    if result["found"]:
        status_color = "green" if result["status"] == "COMPLETED" else "red"
        console.print(f"  Finalize status: [{status_color}]{result['status']}[/]")
        if result["error"]:
            console.print(f"  Error: [red]{result['error']}[/]")
    else:
        console.print("  [yellow]No pipeline run found[/]")
    
    return {
        "nextflow_finalize_status": result["status"],
        "nextflow_logs": result["logs"],
    }


def node_determine_root_cause(state: InvestigationState) -> dict:
    """Node 3: Deterministic root cause decision based on evidence."""
    console.print("\n[bold cyan]→ Determining root cause...[/]")
    
    # Decision tree based on evidence
    marker_missing = state["s3_marker_exists"] is False
    finalize_failed = state["nextflow_finalize_status"] == "FAILED"
    has_logs = state["nextflow_logs"] is not None
    
    if marker_missing and finalize_failed and has_logs:
        # Parse logs for specific error
        logs = state["nextflow_logs"] or ""
        if "AccessDenied" in logs or "permission" in logs.lower():
            root_cause = (
                "The Nextflow finalize step failed due to S3 AccessDenied error. "
                "The IAM role is missing s3:PutObject permission, which prevented "
                "the _SUCCESS marker from being written. Service B loader is blocked."
            )
            confidence = 0.95
        else:
            root_cause = (
                f"The Nextflow finalize step failed, preventing _SUCCESS marker creation. "
                f"Check logs for details."
            )
            confidence = 0.85
    elif marker_missing and finalize_failed:
        root_cause = "Finalize step failed, _SUCCESS marker not written."
        confidence = 0.80
    elif marker_missing:
        root_cause = "_SUCCESS marker missing. Unknown cause."
        confidence = 0.50
    else:
        root_cause = "No clear root cause identified."
        confidence = 0.20
    
    console.print(f"  Root cause: [bold]{root_cause[:80]}...[/]")
    console.print(f"  Confidence: [bold]{confidence:.0%}[/]")

    return {"root_cause": root_cause, "confidence": confidence}


def node_output(state: InvestigationState) -> dict:
    """Node 4: Generate Slack message and problem.md."""
    console.print("\n[bold cyan]→ Generating outputs...[/]")

    # Slack message
    slack = f"""🚨 *Incident Resolved: {state['alert_name']}*

*Severity:* {state['severity']}
*Affected:* {state['affected_table']}

*Root Cause:*
{state['root_cause']}

*Evidence:*
• S3 _SUCCESS marker: {'Missing' if not state['s3_marker_exists'] else 'Present'}
• Nextflow finalize: {state['nextflow_finalize_status']}

*Confidence:* {state['confidence']:.0%}

*Recommended Actions:*
1. [CRITICAL] Fix IAM permissions for s3:PutObject
2. [HIGH] Rerun Nextflow finalize step
"""

    # problem.md
    problem_md = f"""# Incident Report: {state['alert_name']}

## Summary
- **Severity:** {state['severity']}
- **Affected Table:** {state['affected_table']}
- **Confidence:** {state['confidence']:.0%}

## Root Cause
{state['root_cause']}

## Evidence Collected

### S3 Check
- _SUCCESS marker exists: {state['s3_marker_exists']}
- Files in output prefix: {state['s3_file_count']}

### Nextflow Check
- Finalize step status: {state['nextflow_finalize_status']}
- Logs available: {state['nextflow_logs'] is not None}

## Recommended Actions
1. **[CRITICAL]** Fix IAM permissions for s3:PutObject on tracer-processed-data bucket
2. **[HIGH]** Rerun Nextflow finalize step to write _SUCCESS marker
3. **[MEDIUM]** Add alerting on IAM permission failures

## Logs
```
{state['nextflow_logs'] or 'No logs available'}
```
"""

    return {"slack_message": slack, "problem_md": problem_md}


# ─────────────────────────────────────────────────────────────────────────────
# BUILD THE GRAPH
# ─────────────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """Build the investigation state machine."""
    graph = StateGraph(InvestigationState)

    # Add nodes
    graph.add_node("check_s3", node_check_s3)
    graph.add_node("check_nextflow", node_check_nextflow)
    graph.add_node("determine_root_cause", node_determine_root_cause)
    graph.add_node("output", node_output)

    # Add edges (linear flow)
    graph.add_edge(START, "check_s3")
    graph.add_edge("check_s3", "check_nextflow")
    graph.add_edge("check_nextflow", "determine_root_cause")
    graph.add_edge("determine_root_cause", "output")
    graph.add_edge("output", END)

    return graph.compile()


def run_investigation(alert_name: str, affected_table: str, severity: str) -> InvestigationState:
    """Run the investigation graph."""
    console.print(Panel(
        f"[bold]Investigation Started[/]\n\n"
        f"Alert: {alert_name}\n"
        f"Table: {affected_table}\n"
        f"Severity: {severity}",
        title="🔍 LangGraph Investigation",
        border_style="blue"
    ))

    graph = build_graph()

    initial_state: InvestigationState = {
        "alert_name": alert_name,
        "affected_table": affected_table,
        "severity": severity,
        "s3_marker_exists": None,
        "s3_file_count": 0,
        "nextflow_finalize_status": None,
        "nextflow_logs": None,
        "root_cause": None,
        "confidence": 0.0,
        "slack_message": None,
        "problem_md": None,
    }

    # Run the graph
    final_state = graph.invoke(initial_state)

    # Print outputs
    console.print("\n" + "=" * 60)
    console.print(Panel(final_state["slack_message"], title="📢 Slack Message", border_style="green"))

    return final_state

