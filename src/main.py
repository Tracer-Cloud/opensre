"""
Main entry point for the incident resolution agent.

Pure orchestration: load input → run graph → return results.
No filesystem side effects. Use src.presentation.export for persistence.

Runtime configuration is handled by config.config.
"""

# Initialize runtime FIRST, before any other imports
from config.config import init_runtime
runtime_config = init_runtime()

from src.models.alert import GrafanaAlertPayload, normalize_grafana_alert
from src.agent.graph import run_investigation


def main(alert: GrafanaAlertPayload) -> dict:
    """
    Run the incident resolution agent.

    Pure function: returns results in-memory with no filesystem side effects.
    For persistence, use src.presentation.export.export_outputs().

    Args:
        alert: The Grafana alert payload to investigate.

    Returns:
        The final investigation state containing:
            - slack_message: str
            - problem_md: str
            - root_cause: str
            - confidence: float
    """
    # Normalize alert
    normalized = normalize_grafana_alert(alert)

    # Run the graph
    final_state = run_investigation(
        alert_name=normalized.alert_name,
        affected_table=normalized.affected_table or "events_fact",
        severity=normalized.severity,
    )

    return final_state


def is_langsmith_enabled() -> bool:
    """Check if LangSmith tracing is enabled."""
    return runtime_config["langsmith_enabled"]


def get_langsmith_project() -> str | None:
    """Get the LangSmith project name if enabled."""
    return runtime_config["langsmith_project"]
