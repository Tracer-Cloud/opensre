"""
CLI entry point for the incident resolution agent.
"""

from typing import Any

from dotenv import load_dotenv

load_dotenv()

from langsmith import traceable  # noqa: E402

from app.agent.graph_pipeline import run_investigation  # noqa: E402
from app.cli import parse_args, write_json  # noqa: E402
from app.ingest import load_request_from_json  # noqa: E402


# Todo: this is confusing because we now both have a node called investigation and a traceable called investigation
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
        req.pipeline_name,
        req.severity,
        raw_alert=req.raw_alert,
    )
    write_json(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
