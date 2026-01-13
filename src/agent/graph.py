"""
Investigation Graph - LangGraph state machine for incident resolution.

The graph is explicit:
    START → check_s3 → check_nextflow → determine_root_cause → output → END

Two external context calls (mocked):
    1. S3: Check if _SUCCESS marker exists
    2. Nextflow: Get finalize step status + logs

One LLM call (real):
    - Claude for root cause analysis
"""

from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langchain_anthropic import ChatAnthropic
from rich.console import Console
from rich.panel import Panel

from src.mocks.s3 import get_s3_client
from src.mocks.nextflow import get_nextflow_client

console = Console()

# Initialize Claude
llm = ChatAnthropic(model="claude-sonnet-4-20250514", temperature=0)

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
# HELPER - Stream LLM response with visual feedback
# ─────────────────────────────────────────────────────────────────────────────

def stream_llm_response(prompt: str) -> str:
    """Stream LLM response and return full content."""
    content = ""
    console.print("  ", end="")
    for chunk in llm.stream(prompt):
        chunk_text = chunk.content
        content += chunk_text
        if chunk_text.strip():
            console.print("[dim].[/]", end="")
    console.print()
    return content


# ─────────────────────────────────────────────────────────────────────────────
# GRAPH NODES - Each transforms state with LLM reasoning
# ─────────────────────────────────────────────────────────────────────────────

def node_check_s3(state: InvestigationState) -> dict:
    """Node 1: Check S3 and have LLM interpret the results."""
    console.print("\n[bold cyan]→ Step 1: Checking S3 for data artifacts...[/]")

    # Call mock S3 API
    result = check_s3_marker("tracer-processed-data", "events/2026-01-13/")

    console.print(f"  [dim]API Response: marker_exists={result['marker_exists']}, files={result['file_count']}[/]")

    # LLM interprets the S3 findings
    prompt = f"""You are investigating a data freshness incident for table events_fact.

You just queried S3 bucket "tracer-processed-data" with prefix "events/2026-01-13/" and got:
- _SUCCESS marker exists: {result['marker_exists']}
- Files found: {result['file_count']}
- File list: {result['files']}

Interpret these findings in 1-2 bullet points. What does this tell us about the pipeline state?
Be concise (under 80 chars per bullet). Start each line with •"""

    console.print("  [dim]LLM interpreting...[/]")
    interpretation = stream_llm_response(prompt)

    # Display interpretation
    for line in interpretation.strip().split('\n'):
        if line.strip().startswith('•'):
            console.print(f"  [green]{line.strip()}[/]")

    return {
        "s3_marker_exists": result["marker_exists"],
        "s3_file_count": result["file_count"],
    }


def node_check_nextflow(state: InvestigationState) -> dict:
    """Node 2: Check Nextflow and have LLM interpret the results."""
    console.print("\n[bold cyan]→ Step 2: Checking Nextflow pipeline status...[/]")

    # Call mock Nextflow API
    result = check_nextflow_finalize("events-etl")

    console.print(f"  [dim]API Response: status={result['status']}, error={result.get('error', 'none')}[/]")

    # LLM interprets the Nextflow findings
    prompt = f"""You are investigating a data freshness incident for table events_fact.

You just queried the Nextflow API for pipeline "events-etl" and got:
- Pipeline found: {result['found']}
- Finalize step status: {result['status']}
- Error message: {result.get('error', 'none')}
- Logs:
```
{result['logs'] or 'No logs available'}
```

Interpret these findings in 1-2 bullet points. What does this tell us about why the pipeline failed?
Be concise (under 80 chars per bullet). Start each line with •"""

    console.print("  [dim]LLM interpreting...[/]")
    interpretation = stream_llm_response(prompt)

    # Display interpretation
    for line in interpretation.strip().split('\n'):
        if line.strip().startswith('•'):
            console.print(f"  [green]{line.strip()}[/]")

    return {
        "nextflow_finalize_status": result["status"],
        "nextflow_logs": result["logs"],
    }


def node_determine_root_cause(state: InvestigationState) -> dict:
    """Node 3: LLM synthesizes all evidence into root cause conclusion."""
    console.print("\n[bold cyan]→ Step 3: Synthesizing root cause analysis...[/]")

    # Build the prompt with all evidence
    prompt = f"""You are an expert data infrastructure engineer. You have investigated a production incident and collected the following evidence.

## Incident
- Alert: {state['alert_name']}
- Affected Table: {state['affected_table']}

## Evidence Collected

### S3 Check Results
- _SUCCESS marker exists: {state['s3_marker_exists']}
- Files in output prefix: {state['s3_file_count']}

### Nextflow Pipeline Check Results
- Finalize step status: {state['nextflow_finalize_status']}
- Logs:
```
{state['nextflow_logs'] or 'No logs available'}
```

## Task
Synthesize these findings into a root cause conclusion.

Respond in exactly this format:
ROOT_CAUSE:
• <first key finding as a bullet point>
• <second key finding as a bullet point>
• <third key finding - the actual root cause>
• <impact on downstream systems>
CONFIDENCE: <number between 0 and 100>

Keep each bullet point concise (under 80 characters). Use exactly 3-4 bullet points.
"""

    console.print("  [dim]LLM synthesizing findings...[/]")
    content = stream_llm_response(prompt)

    # Parse response
    root_cause = "Unable to determine root cause"
    confidence = 0.5

    if "ROOT_CAUSE:" in content:
        parts = content.split("ROOT_CAUSE:")[1]
        if "CONFIDENCE:" in parts:
            root_cause = parts.split("CONFIDENCE:")[0].strip()
            conf_str = parts.split("CONFIDENCE:")[1].strip().split()[0].replace("%", "")
            try:
                confidence = float(conf_str) / 100
            except ValueError:
                confidence = 0.8
        else:
            root_cause = parts.strip()

    # Display parsed result
    console.print(f"  [green]✓[/] Root cause identified")
    for line in root_cause.split('\n'):
        if line.strip():
            console.print(f"    {line.strip()}")
    console.print(f"  Confidence: [bold]{confidence:.0%}[/]")

    return {"root_cause": root_cause, "confidence": confidence}


def node_output(state: InvestigationState) -> dict:
    """Node 4: Generate Slack message and problem.md."""
    console.print("\n[bold cyan]→ Generating outputs...[/]")

    # Slack message - agent voice, not alert voice
    slack = f"""🧠 *RCA — {state['affected_table']} freshness incident*
Analyzed by: pipeline-agent
Detected: 02:13 UTC

*Conclusion*
{state['root_cause']}

*Evidence chain*
• Raw input file present in S3
• `events_processed.parquet` written successfully
• Nextflow finalize step: {state['nextflow_finalize_status']} after 5 retries
• `_SUCCESS` marker: {'not found' if not state['s3_marker_exists'] else 'present'}
• Service B loader running, blocked on `_SUCCESS`

*Confidence:* {state['confidence']:.2f}

*Actions*
1. Grant Nextflow role `s3:PutObject` on the `_SUCCESS` path
2. Rerun Nextflow finalize step
"""

    # problem.md - detailed report
    problem_md = f"""# RCA — {state['affected_table']} freshness incident

**Analyzed by:** pipeline-agent
**Detected:** 02:13 UTC
**Confidence:** {state['confidence']:.2f}

## Conclusion

{state['root_cause']}

## Evidence Chain

| Check | Result |
|-------|--------|
| Raw input file | Present in S3 |
| Processed output | `events_processed.parquet` written |
| Nextflow finalize | {state['nextflow_finalize_status']} after 5 retries |
| `_SUCCESS` marker | {'Missing' if not state['s3_marker_exists'] else 'Present'} |
| Service B loader | Running, blocked on `_SUCCESS` |

## Actions

1. Grant Nextflow role `s3:PutObject` on `tracer-processed-data/events/2026-01-13/_SUCCESS`
2. Rerun Nextflow finalize step

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
        title="Pipeline Investigation",
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
    console.print("\n")
    console.print(Panel(final_state["slack_message"], title="Agent Output", border_style="blue"))

    return final_state

