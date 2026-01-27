import os

from app.agent.nodes.build_context.context_building import build_investigation_context


def test_build_investigation_context_tracer_web_integration() -> None:
    jwt_token = os.getenv("JWT_TOKEN")
    assert jwt_token, "JWT_TOKEN must be set for this integration test"

    context = build_investigation_context({"plan_sources": ["tracer_web"]})

    tracer_web_run = context.get("tracer_web_run")
    assert tracer_web_run is not None
    assert isinstance(tracer_web_run.get("found"), bool)
