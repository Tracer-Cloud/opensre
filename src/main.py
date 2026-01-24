"""
Generic CLI for the incident resolution agent.

Reads a Grafana alert payload from an input JSON file or stdin and runs the
investigation graph, outputting JSON results to stdout.

For the demo with Rich console output, use: python examples/run_demo.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from src.schemas.alert import GrafanaAlertPayload


def init() -> None:
    # Initialize runtime before importing anything that depends on it
    from config import init_runtime

    init_runtime()


class InvestigationResult(TypedDict):
    slack_message: str
    problem_md: str
    root_cause: str
    confidence: float


def read_json(path: str | None) -> dict[str, Any]:
    if path in (None, "-"):
        return json.load(sys.stdin)

    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(data: dict[str, Any], path: str | None) -> None:
    if path:
        Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return

    json.dump(data, sys.stdout, indent=2)
    sys.stdout.write("\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run incident resolution agent on a Grafana alert payload."
    )
    p.add_argument(
        "--input",
        "-i",
        default="-",
        help="Path to JSON file containing Grafana alert payload. Use - for stdin.",
    )
    p.add_argument(
        "--output",
        "-o",
        default=None,
        help="Path to output JSON file. Defaults to stdout.",
    )
    return p.parse_args(argv)


def run(alert: "GrafanaAlertPayload") -> InvestigationResult:
    from langsmith import traceable
    from src.agent.graph import run_investigation
    from src.schemas.alert import normalize_grafana_alert

    @traceable
    def _run(a: "GrafanaAlertPayload") -> InvestigationResult:
        normalized = normalize_grafana_alert(a)

        state = run_investigation(
            alert_name=normalized.alert_name,
            affected_table=normalized.affected_table or "events_fact",
            severity=normalized.severity,
        )

        return {
            "slack_message": state["slack_message"],
            "problem_md": state["problem_md"],
            "root_cause": state["root_cause"],
            "confidence": state["confidence"],
        }

    return _run(alert)


def main(argv: list[str] | None = None) -> int:
    init()

    from src.schemas.alert import GrafanaAlertPayload

    args = parse_args(argv)
    payload = read_json(args.input)
    alert = GrafanaAlertPayload(**payload)

    output = run(alert)
    write_json(output, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
