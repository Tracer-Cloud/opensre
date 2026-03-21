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
        "note": "Dry-run only previews execution. No LLM, tool, or external API calls were made.",
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

    alert_name = payload.get("alert_name") or "Incident"
    pipeline_name = payload.get("pipeline_name") or "events_fact"
    severity = payload.get("severity") or "warning"

    if getattr(args, "dry_run", False):
        preview = _build_dry_run_preview(
            alert_name=alert_name,
            pipeline_name=pipeline_name,
            severity=severity,
            raw_alert=payload,
        )
        write_json(preview, args.output)
        return 0

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
