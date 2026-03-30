"""Alert payload loading helpers for CLI investigations."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def parse_payload_text(raw_text: str, source_label: str) -> dict[str, Any]:
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


def load_stdin_payload() -> dict[str, Any]:
    """Read a JSON payload from stdin."""
    if sys.stdin.isatty():
        raise SystemExit(
            "No alert input provided on stdin. Use --interactive, --input <file>, or --input-json."
        )
    return parse_payload_text(sys.stdin.read(), "stdin")


def load_interactive_payload() -> dict[str, Any]:
    """Prompt the user to paste an alert payload."""
    print(
        "Paste the alert JSON payload, then press Ctrl-D when finished.",
        file=sys.stderr,
    )
    raw_text = sys.stdin.read()
    if not raw_text.strip():
        raise SystemExit("No alert JSON was provided in interactive mode.")
    return parse_payload_text(raw_text, "interactive input")


def load_file_payload(path_str: str) -> dict[str, Any]:
    """Read and parse an alert payload from a local file."""
    try:
        raw_text = Path(path_str).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(f"Alert JSON file not found: {path_str}") from exc
    except UnicodeDecodeError as exc:
        raise SystemExit(f"Alert JSON file must be UTF-8 text: {path_str}") from exc
    except OSError as exc:
        raise SystemExit(f"Could not read alert JSON file {path_str}: {exc}") from exc

    return parse_payload_text(raw_text, path_str)


def load_payload(
    input_path: str | None,
    input_json: str | None,
    interactive: bool,
) -> dict[str, Any]:
    """Load raw alert payload from file, stdin, inline JSON, or interactive paste."""
    if input_json:
        return parse_payload_text(input_json, "--input-json")
    if interactive:
        return load_interactive_payload()
    if input_path == "-":
        return load_stdin_payload()
    if input_path:
        return load_file_payload(input_path)
    if sys.stdin.isatty():
        raise SystemExit(
            "No alert input provided. Use --interactive, --input <file>, --input-json, or pipe JSON to stdin."
        )
    return load_stdin_payload()
