import os

from dotenv import load_dotenv

from src.agent.nodes.collect_evidence import node_collect_evidence
from src.agent.state import InvestigationState


def test_collect_evidence_finds_failed_run_via_web_app() -> None:
    load_dotenv()
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
