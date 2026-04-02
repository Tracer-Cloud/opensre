"""Shared investigation helpers for CLI entrypoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langsmith import traceable

from app.config import LLMSettings

if TYPE_CHECKING:
    from app.state import AgentState


def _call_run_investigation(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    *,
    raw_alert: dict[str, Any],
) -> AgentState:
    """Import the heavy investigation runner only when execution starts."""
    from app.runners import run_investigation

    return run_investigation(
        alert_name,
        pipeline_name,
        severity,
        raw_alert=raw_alert,
    )


def resolve_investigation_context(
    *,
    raw_alert: dict[str, Any],
    alert_name: str | None,
    pipeline_name: str | None,
    severity: str | None,
) -> tuple[str, str, str]:
    """Resolve investigation metadata from CLI overrides and payload defaults."""
    return (
        alert_name or raw_alert.get("alert_name") or "Incident",
        pipeline_name or raw_alert.get("pipeline_name") or "events_fact",
        severity or raw_alert.get("severity") or "warning",
    )


@traceable(name="investigation")
def run_investigation_cli(
    *,
    raw_alert: dict[str, Any],
    alert_name: str | None = None,
    pipeline_name: str | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    """Run the investigation and return the CLI-facing JSON payload."""
    LLMSettings.from_env()
    resolved_alert_name, resolved_pipeline_name, resolved_severity = resolve_investigation_context(
        raw_alert=raw_alert,
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
    )
    state = _call_run_investigation(
        resolved_alert_name,
        resolved_pipeline_name,
        resolved_severity,
        raw_alert=raw_alert,
    )
    slack_message = state["slack_message"]
    return {
        "slack_message": slack_message,
        "report": slack_message,
        "problem_md": state["problem_md"],
        "root_cause": state["root_cause"],
    }
