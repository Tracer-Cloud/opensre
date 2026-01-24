"""
CLI entry point for the incident resolution agent.

For the demo with Rich console output, use: python tests/run_demo.py
"""

from config import init_runtime

init_runtime()

from typing import Any

from langsmith import traceable  # noqa: E402

from src.agent.graph_pipeline import run_investigation_pipeline  # noqa: E402
from src.cli import parse_args, write_json  # noqa: E402
from src.ingest import load_request_from_json  # noqa: E402


@traceable(name="investigation")
def _run(
    alert_name: str,
    affected_table: str,
    severity: str,
    raw_alert: dict[str, Any],
) -> dict:
    state = run_investigation_pipeline(
        alert_name,
        affected_table,
        severity,
        raw_alert=raw_alert,
    )
    return {
        "slack_message": state["slack_message"],
        "problem_md": state["problem_md"],
        "root_cause": state["root_cause"],
        "confidence": state["confidence"],
    }


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args = parse_args(argv)
    req = load_request_from_json(args.input)
    result = _run(
        req.alert_name,
        req.affected_table,
        req.severity,
        raw_alert=req.raw_alert,
    )
    write_json(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
