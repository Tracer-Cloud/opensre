"""
Superfluid Use Case - Pure Business Logic.

Find a failed pipeline run from Tracer Web App.
No orchestration, no alert creation, no investigation logic.
"""

from app.agent.nodes.build_context.context_building import _fetch_tracer_web_run_context

_run_context = {
    "pipeline_name": None,
    "run_name": None,
    "trace_id": None,
    "status": None,
    "run_url": None,
    "found": False,
}


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
    """
    web_run = _fetch_tracer_web_run_context()

    if web_run.get("found"):
        _run_context["pipeline_name"] = web_run.get("pipeline_name")
        _run_context["run_name"] = web_run.get("run_name")
        _run_context["trace_id"] = web_run.get("trace_id")
        _run_context["status"] = web_run.get("status")
        _run_context["run_url"] = web_run.get("run_url")
        _run_context["found"] = True

    return web_run
