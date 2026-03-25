"""
CLI entry point for the incident resolution agent.
"""

import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(override=False)

from langsmith import traceable  # noqa: E402

from app.agent.runners import run_investigation  # noqa: E402
from app.agent.tools.tool_actions.investigation_registry import get_available_actions  # noqa: E402
from app.cli import parse_args, write_json  # noqa: E402


def _load_payload(path: str | None) -> dict[str, Any]:
    """Load raw alert payload from JSON file or stdin."""
    if path is None or path == "-":
        data: Any = json.load(sys.stdin)
    else:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("Input payload must be a JSON object")
    return data


def _extract_alert_fields_for_dry_run(raw_alert: dict[str, Any]) -> tuple[str, str, str]:
    """
    Deterministic alert extraction for dry-run mode.
    Supports top level, Alertmanager/Grafana payloads
    - alerts[0].labels
    - commonLabels
    - annotations/commonAnnotations
    """

    alerts = raw_alert.get("alerts", [])
    first_alert = (
        alerts[0] if isinstance(alerts, list) and alerts and isinstance(alerts[0], dict) else {}
    )

    top_labels = raw_alert.get("labels", {})
    first_labels = first_alert.get("labels", {})
    common_labels = raw_alert.get("commonLabels", {})

    top_annotations = raw_alert.get("annotations", {})
    first_annotations = first_alert.get("annotations", {})
    common_annotations = raw_alert.get("commonAnnotations", {})

    if not isinstance(top_labels, dict):
        top_labels = {}
    if not isinstance(first_labels, dict):
        first_labels = {}
    if not isinstance(common_labels, dict):
        common_labels = {}
    if not isinstance(top_annotations, dict):
        top_annotations = {}
    if not isinstance(first_annotations, dict):
        first_annotations = {}
    if not isinstance(common_annotations, dict):
        common_annotations = {}
    alert_name = (
        raw_alert.get("alert_name")
        or top_labels.get("alertname")
        or first_labels.get("alertname")
        or common_labels.get("alertname")
        or "Incident"
    )
    pipeline_name = (
        raw_alert.get("pipeline_name")
        or top_labels.get("pipeline_name")
        or top_labels.get("pipeline")
        or top_labels.get("table")
        or first_labels.get("pipeline_name")
        or first_labels.get("pipeline")
        or first_labels.get("table")
        or common_labels.get("pipeline_name")
        or common_labels.get("pipeline")
        or common_labels.get("table")
        or first_annotations.get("pipeline_name")
        or top_annotations.get("pipeline_name")
        or common_annotations.get("pipeline_name")
        or "events_fact"
    )
    severity = (
        raw_alert.get("severity")
        or top_labels.get("severity")
        or first_labels.get("severity")
        or common_labels.get("severity")
        or "warning"
    )

    return str(alert_name), str(pipeline_name), str(severity)


def _build_dry_run_preview(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    raw_alert: dict[str, Any],
) -> dict[str, Any]:
    """Build a dry-run preview without executing investigation logic."""
    planned_steps = [
        "extract_alert",
        "resolve_integrations",
        "plan_actions",
        "investigate",
        "root_cause_diagnosis",
        "publish_findings",
    ]

    # Optional hints from payload only; no API/LLM/tool execution.
    payload_keys = sorted(raw_alert.keys())
    candidate_integrations = raw_alert.get("integrations", [])
    if not isinstance(candidate_integrations, list):
        candidate_integrations = []
    potential_tools = [action.name for action in get_available_actions()]

    return {
        "mode": "dry-run",
        "no_external_calls": True,
        "resolved_input": {
            "alert_name": alert_name,
            "pipeline_name": pipeline_name,
            "severity": severity,
        },
        "payload_summary": {
            "keys": payload_keys,
            "integration_count": len(candidate_integrations),
        },
        "planned_steps": planned_steps,
        "potential_tools": potential_tools,
        "note": "No integrations, LLM, tool execution, or external API calls were made.",
    }


@traceable(name="investigation")
def _run(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    raw_alert: dict[str, Any],
) -> dict:
    state = run_investigation(
        alert_name,
        pipeline_name,
        severity,
        raw_alert=raw_alert,
    )
    return {
        "slack_message": state["slack_message"],
        "report": state["slack_message"],
        "problem_md": state["problem_md"],
        "root_cause": state["root_cause"],
    }


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args = parse_args(argv)
    payload = _load_payload(args.input)

    if getattr(args, "dry_run", False):
        alert_name, pipeline_name, severity = _extract_alert_fields_for_dry_run(payload)
        preview = _build_dry_run_preview(
            alert_name=alert_name,
            pipeline_name=pipeline_name,
            severity=severity,
            raw_alert=payload,
        )
        write_json(preview, args.output)
        return 0

    alert_name = payload.get("alert_name") or "Incident"
    pipeline_name = payload.get("pipeline_name") or "events_fact"
    severity = payload.get("severity") or "warning"

    result = _run(
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
        raw_alert=payload,
    )
    write_json(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
