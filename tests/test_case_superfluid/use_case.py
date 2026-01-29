"""
Superfluid Use Case - Fetch Failed Run from Tracer.

Pure business logic: Find a failed pipeline run from Tracer Web App.
No orchestration, no alert creation, no investigation logic.
"""

from app.agent.nodes.build_context.context_building import _fetch_tracer_web_run_context


def find_failed_run() -> dict:
    """
    Find a real failed pipeline run from Tracer Web App.

    Returns:
        Dictionary with run details:
        - found: bool
        - pipeline_name: str
        - run_name: str
        - trace_id: str | None
        - status: str
        - run_url: str | None
        - pipelines_checked: int
    """
    web_run = _fetch_tracer_web_run_context()
    return web_run
