"""
CLI for the incident resolution agent.

Thin wiring layer: parses args, calls ingest, runs agent, writes output.
For the demo with Rich console output, use: python tests/run_demo.py
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TypedDict


def init() -> None:
    """Initialize runtime before importing modules that depend on it."""
    from config import init_runtime
    init_runtime()


class InvestigationResult(TypedDict):
    """Output schema for the investigation."""
    slack_message: str
    problem_md: str
    root_cause: str
    confidence: float


def write_json(data: dict[str, Any], path: str | None) -> None:
    """Write JSON to file or stdout."""
    if path:
        Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return
    json.dump(data, sys.stdout, indent=2)
    sys.stdout.write("\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    p = argparse.ArgumentParser(
        description="Run incident resolution agent on a Grafana alert payload."
    )
    p.add_argument(
        "--input", "-i",
        default="-",
        help="Path to JSON file containing Grafana alert payload. Use - for stdin.",
    )
    p.add_argument(
        "--output", "-o",
        default=None,
        help="Path to output JSON file. Defaults to stdout.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    init()

    from langsmith import traceable

    from src.agent.graph import run_investigation
    from src.ingest import load_request_from_json

    args = parse_args(argv)
    request = load_request_from_json(args.input)

    @traceable(name="investigation")
    def run_traced() -> InvestigationResult:
        state = run_investigation(
            alert_name=request.alert_name,
            affected_table=request.affected_table,
            severity=request.severity,
        )
        return {
            "slack_message": state["slack_message"],
            "problem_md": state["problem_md"],
            "root_cause": state["root_cause"],
            "confidence": state["confidence"],
        }

    result = run_traced()
    write_json(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
