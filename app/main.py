"""
CLI entry point for the incident resolution agent.
"""

import json
import sys
from pathlib import Path
from typing import Any

from config.grafana_config import load_env

load_env()

from langsmith import traceable  # noqa: E402

from app.agent.runners import run_investigation  # noqa: E402
from app.cli import parse_args, write_json  # noqa: E402


def _load_payload(path: str | None) -> dict[str, Any]:
    """Load raw alert payload from JSON file or stdin."""
    if path is None or path == "-":
        data: Any = json.load(sys.stdin)
    else:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


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
    # Slack delivery is now handled inside node_publish_findings
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
