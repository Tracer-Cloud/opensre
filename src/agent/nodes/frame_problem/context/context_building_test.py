import os

from src.agent.nodes.frame_problem.context.context_building import build_investigation_context
from src.agent.tools.clients import tracer_client as tracer_client_module


def test_build_investigation_context_tracer_web_integration() -> None:
    jwt_token = os.getenv("JWT_TOKEN")
    assert jwt_token, "JWT_TOKEN must be set for this integration test"

    context = build_investigation_context({"plan_sources": ["tracer_web"]})

    tracer_web_run = context.get("tracer_web_run")
    assert tracer_web_run is not None
    assert isinstance(tracer_web_run.get("found"), bool)


def test_build_investigation_context_records_missing_jwt_token(monkeypatch) -> None:
    monkeypatch.delenv("JWT_TOKEN", raising=False)
    monkeypatch.setattr(tracer_client_module, "_tracer_client", None)

    context = build_investigation_context({"plan_sources": ["tracer_web"]})

    tracer_web_run = context.get("tracer_web_run", {})
    assert tracer_web_run.get("found") is False
    assert tracer_web_run.get("error")

    context_errors = context.get("context_errors", [])
    assert context_errors
    assert context_errors[0].get("source") == "tracer_web"
