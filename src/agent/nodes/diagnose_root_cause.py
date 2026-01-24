"""Diagnose root cause from collected evidence."""

from src.agent.state import InvestigationState
from src.agent.tools.llm import parse_root_cause, stream_completion


def node_diagnose_root_cause(state: InvestigationState) -> dict:
    """Synthesize evidence into root cause using LLM."""
    prompt = _build_prompt(state, state.get("evidence", {}))
    result = parse_root_cause(stream_completion(prompt))
    return {"root_cause": result.root_cause, "confidence": result.confidence}


def _build_prompt(state: InvestigationState, evidence: dict) -> str:
    """Build analysis prompt from evidence."""
    # Format each evidence section
    s3 = evidence.get("s3", {})
    s3_info = f"- Marker: {s3.get('marker_exists')}, Files: {s3.get('file_count', 0)}" if s3.get("found") else "No S3 data"

    run = evidence.get("pipeline_run", {})
    run_info = "No pipeline data"
    if run.get("found"):
        run_info = f"""- Pipeline: {run.get('pipeline_name')} | Status: {run.get('status')}
- Duration: {run.get('run_time_minutes', 0)}min | Cost: ${run.get('run_cost_usd', 0)}
- User: {run.get('user_email')} | Team: {run.get('team')}"""

    batch = evidence.get("batch_jobs", {})
    batch_info = "No batch data"
    if batch.get("found"):
        batch_info = f"- Jobs: {batch.get('total_jobs')} total, {batch.get('failed_jobs')} failed"
        if batch.get("failure_reason"):
            batch_info += f"\n- Failure: {batch['failure_reason']}"

    return f"""Analyze this incident and determine root cause.

## Incident
Alert: {state['alert_name']} | Table: {state['affected_table']}

## Evidence
### Pipeline: {run_info}
### Batch: {batch_info}
### S3: {s3_info}

Respond in this format:
ROOT_CAUSE:
* <finding 1>
* <finding 2>
* <finding 3>
CONFIDENCE: <0-100>"""

