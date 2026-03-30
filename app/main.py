"""
CLI entry point for the incident resolution agent.
"""

import json
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(override=False)

from langsmith import traceable  # noqa: E402

from app.agent.runners import run_investigation  # noqa: E402
from app.alert_templates import build_alert_template  # noqa: E402
from app.cli import parse_args, write_json  # noqa: E402


def _parse_payload_text(raw_text: str, source_label: str) -> dict[str, Any]:
    """Parse and validate a JSON object payload."""
    try:
        data: Any = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Invalid alert JSON from {source_label}: {exc.msg} at line {exc.lineno}, column {exc.colno}."
        ) from exc

    if not isinstance(data, dict):
        raise SystemExit(f"Alert payload from {source_label} must be a JSON object.")

    return data


def _read_stdin_payload() -> dict[str, Any]:
    """Read a JSON payload from stdin."""
    if sys.stdin.isatty():
        raise SystemExit(
            "No alert input provided on stdin. Use --interactive, --input <file>, or --input-json."
        )
    return _parse_payload_text(sys.stdin.read(), "stdin")


def _read_interactive_payload() -> dict[str, Any]:
    """Prompt the user to paste an alert payload."""
    print(
        "Paste the alert JSON payload, then press Ctrl-D when finished.",
        file=sys.stderr,
    )
    raw_text = sys.stdin.read()
    if not raw_text.strip():
        raise SystemExit("No alert JSON was provided in interactive mode.")
    return _parse_payload_text(raw_text, "interactive input")


def _read_file_payload(path_str: str) -> dict[str, Any]:
    """Read and parse an alert payload from a local file."""
    try:
        raw_text = Path(path_str).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(f"Alert JSON file not found: {path_str}") from exc
    except UnicodeDecodeError as exc:
        raise SystemExit(f"Alert JSON file must be UTF-8 text: {path_str}") from exc
    except OSError as exc:
        raise SystemExit(f"Could not read alert JSON file {path_str}: {exc}") from exc

    return _parse_payload_text(raw_text, path_str)


def _load_payload(args: Namespace) -> dict[str, Any]:
    """Load raw alert payload from file, stdin, inline JSON, or interactive paste."""
    if args.input_json:
        return _parse_payload_text(args.input_json, "--input-json")
    if args.interactive:
        return _read_interactive_payload()
    if args.input == "-":
        return _read_stdin_payload()
    if args.input:
        return _read_file_payload(args.input)
    if sys.stdin.isatty():
        raise SystemExit(
            "No alert input provided. Use --interactive, --input <file>, --input-json, or pipe JSON to stdin."
        )
    return _read_stdin_payload()


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
    if args.print_template:
        write_json(build_alert_template(args.print_template), args.output)
        return 0

    payload = _load_payload(args)

    alert_name = args.alert_name or payload.get("alert_name") or "Incident"
    pipeline_name = args.pipeline_name or payload.get("pipeline_name") or "events_fact"
    severity = args.severity or payload.get("severity") or "warning"

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
