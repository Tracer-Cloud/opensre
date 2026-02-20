"""
Superfluid Use Case - Pure Business Logic.

Find a failed pipeline run from Tracer Web App.
No orchestration, no alert creation, no investigation logic.
"""

import os
import uuid

from app.agent.nodes.build_context.utils import call_safe
from app.agent.tools.tool_actions.tracer.tracer_runs import fetch_failed_run
from tracer_telemetry import init_telemetry

_run_context = {
    "pipeline_name": None,
    "run_name": None,
    "trace_id": None,
    "status": None,
    "run_url": None,
    "found": False,
}

# Initialize telemetry
_telemetry = None
_tracer = None


def _fetch_failed_run_with_timeout() -> dict:
    pipeline_hint = os.getenv("TRACER_PIPELINE_NAME") or None
    outcome = call_safe(fetch_failed_run, pipeline_name=pipeline_hint)
    if outcome.error:
        return {
            "found": False,
            "error": outcome.error,
            "pipelines_checked": 0,
        }
    if outcome.result is None:
        return {
            "found": False,
            "error": "No context returned",
            "pipelines_checked": 0,
        }
    return outcome.result


def main() -> dict:
    """
    Find a real failed pipeline run from Tracer Web App.

    Returns:
        Dictionary with run details:
        - found: bool
        - pipeline_name: str | None
        - run_name: str | None
        - trace_id: str | None
        - status: str | None
        - run_url: str | None
        - pipelines_checked: int
        - execution_run_id: str
    """
    global _telemetry, _tracer

    # Initialize telemetry
    _telemetry = init_telemetry(
        service_name="superfluid-pipeline",
        resource_attributes={
            "pipeline.name": "superfluid_fetch_runs",
            "pipeline.type": "api",
        },
    )
    _tracer = _telemetry.tracer

    execution_run_id = str(uuid.uuid4())

    with _tracer.start_as_current_span("fetch_tracer_runs") as root_span:
        root_span.set_attribute("execution.run_id", execution_run_id)
        root_span.set_attribute("pipeline.name", "superfluid_fetch_runs")

        with _tracer.start_as_current_span("api_call_tracer_web") as api_span:
            api_span.set_attribute("execution.run_id", execution_run_id)
            web_run = _fetch_failed_run_with_timeout()

            api_span.set_attribute("found", web_run.get("found", False))
            api_span.set_attribute("pipelines_checked", web_run.get("pipelines_checked", 0))

        if web_run.get("found"):
            _run_context["pipeline_name"] = web_run.get("pipeline_name")
            _run_context["run_name"] = web_run.get("run_name")
            _run_context["trace_id"] = web_run.get("trace_id")
            _run_context["status"] = web_run.get("status")
            _run_context["run_url"] = web_run.get("run_url")
            _run_context["found"] = True

            root_span.set_attribute("found_pipeline", web_run.get("pipeline_name", ""))
            root_span.set_attribute("found_run", web_run.get("run_name", ""))

        root_span.set_attribute("status", "success" if web_run.get("found") else "no_runs_found")

    # Flush telemetry for short-lived process
    _telemetry.flush()

    web_run["execution_run_id"] = execution_run_id
    return web_run
