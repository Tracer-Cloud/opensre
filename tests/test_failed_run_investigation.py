"""End-to-end test for investigating a specific failed run."""

import os

from src.agent.context.context_building import _fetch_tracer_web_run_context
from src.agent.graph_pipeline import run_investigation_pipeline
from src.agent.nodes.diagnose_root_cause import node_diagnose_root_cause
from src.agent.nodes.hypothesis_investigation.evidence_gathering import gather_evidence_for_trace
from src.agent.state import InvestigationState
from src.agent.tools.tracer_client import get_tracer_web_client


def test_investigate_specific_failed_run() -> None:
    """Test investigation of shimmering-okapi-891 (trace: a4b56a5c-03c5-438f-96b6-60f8db7c13d5)."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    jwt_token = os.getenv("JWT_TOKEN")
    org_id = os.getenv("TRACER_ORG_ID")
    web_url = os.getenv("TRACER_WEB_APP_URL")
    assert api_key, "ANTHROPIC_API_KEY must be set"
    assert jwt_token, "JWT_TOKEN must be set"
    assert org_id, "TRACER_ORG_ID must be set"
    assert web_url, "TRACER_WEB_APP_URL must be set"

    # Investigate the specific failed run
    trace_id = "a4b56a5c-03c5-438f-96b6-60f8db7c13d5"
    # Find the run with matching trace_id
    client = get_tracer_web_client()
    pipelines = client.get_pipelines(page=1, size=50)
    failed_run = None
    for pipeline in pipelines:
        runs = client.get_pipeline_runs(pipeline.pipeline_name, page=1, size=50)
        for run in runs:
            if run.trace_id == trace_id:
                failed_run = run
                break
        if failed_run:
            break
    if not failed_run:
        raise AssertionError(f"Expected to find trace {trace_id}")
    # Build context
    from src.agent.context.context_building import build_tracer_run_url

    run_url = build_tracer_run_url(failed_run.pipeline_name, trace_id)
    web_run = {
        "found": True,
        "pipeline_name": failed_run.pipeline_name,
        "run_id": failed_run.run_id,
        "run_name": failed_run.run_name,
        "trace_id": trace_id,
        "status": failed_run.status,
        "start_time": failed_run.start_time,
        "end_time": failed_run.end_time,
        "run_cost": failed_run.run_cost,
        "tool_count": failed_run.tool_count,
        "user_email": failed_run.user_email,
        "instance_type": failed_run.instance_type,
        "region": failed_run.region,
        "log_file_count": failed_run.log_file_count,
        "run_url": run_url,
    }
    # Gather evidence
    evidence = gather_evidence_for_trace(trace_id, {})
    web_run.update(evidence)

    assert web_run.get("found"), f"Expected to find trace {trace_id}"
    assert web_run.get("run_name") == "shimmering-okapi-891"
    assert web_run.get("pipeline_name") == "superfluid_prod_pipeline"

    # Verify detailed investigation data
    failed_jobs = web_run.get("failed_jobs", [])
    assert len(failed_jobs) > 0, "Expected failed jobs"

    # Check specific failure details we know about
    star_job = next((j for j in failed_jobs if j.get("job_name") == "STAR_S27"), None)
    assert star_job, "Expected STAR_S27 job in failed jobs"
    assert star_job.get("exit_code") == 102, f"Expected exit code 102, got {star_job.get('exit_code')}"
    assert "Essential container" in star_job.get("status_reason", ""), "Expected 'Essential container' in status reason"

    # Now test root cause analysis with this evidence
    state: InvestigationState = {
        "alert_name": "Pipeline failure: superfluid_prod_pipeline",
        "affected_table": "superfluid_prod_pipeline",
        "severity": "critical",
        "evidence": {"tracer_web_run": web_run},
    }

    result = node_diagnose_root_cause(state)
    root_cause = result.get("root_cause", "")
    confidence = result.get("confidence", 0.0)

    assert root_cause, "Expected root cause to be identified"
    assert confidence > 0.0, "Expected confidence > 0"

    # Verify root cause mentions the key failure indicators
    root_cause_lower = root_cause.lower()
    assert (
        "exit" in root_cause_lower
        or "102" in root_cause_lower
        or "container" in root_cause_lower
        or "essential" in root_cause_lower
    ), f"Root cause should mention exit code or container failure. Got: {root_cause[:300]}"


def test_investigate_failed_run_shimmering_okapi() -> None:
    """Test full pipeline investigation of shimmering-okapi-891 failed run."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    jwt_token = os.getenv("JWT_TOKEN")
    org_id = os.getenv("TRACER_ORG_ID")
    web_url = os.getenv("TRACER_WEB_APP_URL")
    assert api_key, "ANTHROPIC_API_KEY must be set"
    assert jwt_token, "JWT_TOKEN must be set"
    assert org_id, "TRACER_ORG_ID must be set"
    assert web_url, "TRACER_WEB_APP_URL must be set"

    # Create an alert that would trigger investigation
    state = run_investigation_pipeline(
        alert_name="Pipeline failure detected",
        affected_table="superfluid_prod_pipeline",
        severity="critical",
        raw_alert={"message": "Pipeline superfluid_prod_pipeline failed"},
    )

    # Verify evidence was collected
    evidence = state.get("evidence", {})
    web_run = evidence.get("tracer_web_run", {})
    assert web_run.get("found"), "Expected to find failed run"

    # Verify detailed investigation data
    failed_jobs = web_run.get("failed_jobs", [])
    assert len(failed_jobs) > 0, "Expected failed jobs in investigation"

    # Verify root cause was identified
    root_cause = state.get("root_cause", "")
    assert root_cause, "Expected root cause to be identified"

    # Verify the root cause mentions key failure indicators
    root_cause_lower = root_cause.lower()
    assert (
        "job" in root_cause_lower
        or "container" in root_cause_lower
        or "exit" in root_cause_lower
        or "fail" in root_cause_lower
    ), f"Root cause should mention failure details. Got: {root_cause[:200]}"

    # Verify confidence is reasonable
    confidence = state.get("confidence", 0.0)
    assert confidence > 0.0, "Expected confidence > 0"
    assert confidence <= 1.0, "Expected confidence <= 1.0"
