import os

from src.agent.nodes.collect_evidence.collect_evidence import node_collect_evidence
from src.agent.state import InvestigationState


def test_collect_evidence_finds_failed_run_via_web_app() -> None:
    jwt_token = os.getenv("JWT_TOKEN")
    org_id = os.getenv("TRACER_ORG_ID")
    web_url = os.getenv("TRACER_WEB_APP_URL")
    assert jwt_token, "JWT_TOKEN must be set for this integration test"
    assert org_id, "TRACER_ORG_ID must be set for this integration test"
    assert web_url, "TRACER_WEB_APP_URL must be set for this integration test"

    state: InvestigationState = {"plan_sources": ["tracer_web"]}
    result = node_collect_evidence(state)
    evidence = result.get("evidence", {})
    web_run = evidence.get("tracer_web_run", {})

    assert web_run.get("found"), "Expected to find a failed run via web app"
    assert web_run.get("pipeline_name")
    assert web_run.get("trace_id")
    assert str(web_run.get("status", "")).lower() in ("failed", "error")


def test_collect_evidence_investigates_specific_failed_run() -> None:
    """Test investigation of a known failed run: shimmering-okapi-891."""
    jwt_token = os.getenv("JWT_TOKEN")
    org_id = os.getenv("TRACER_ORG_ID")
    web_url = os.getenv("TRACER_WEB_APP_URL")
    assert jwt_token, "JWT_TOKEN must be set for this integration test"
    assert org_id, "TRACER_ORG_ID must be set for this integration test"
    assert web_url, "TRACER_WEB_APP_URL must be set for this integration test"

    state: InvestigationState = {"plan_sources": ["tracer_web"]}
    result = node_collect_evidence(state)
    evidence = result.get("evidence", {})
    web_run = evidence.get("tracer_web_run", {})

    assert web_run.get("found"), "Expected to find a failed run"

    # Verify detailed investigation data is collected
    failed_jobs = web_run.get("failed_jobs", [])
    assert len(failed_jobs) > 0, "Expected to find failed jobs"

    # Check that we have job failure details
    for job in failed_jobs:
        assert job.get("job_name")
        assert job.get("status_reason") or job.get("container_reason") or job.get("exit_code")

    # Verify batch stats are included
    batch_stats = web_run.get("batch_stats", {})
    assert batch_stats.get("failed_job_count", 0) > 0

    # Verify we have tools data
    assert "total_tools" in web_run
    assert "total_jobs" in web_run
