"""Helpers for loading alert payloads from various input sources."""

from __future__ import annotations

import json
import re
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


def load_file(path_str: str) -> dict[str, Any]:
    """Load an alert payload from any text file.

    - ``.json`` — parsed directly as JSON.
    - ``.md`` / ``.txt`` / other — first ```json``` block is extracted and parsed;
      if none is found, raw content is passed as ``{"raw_text": ...}`` for the agent.
    """
    try:
        raw_text = Path(path_str).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(f"Alert file not found: {path_str}") from exc
    except UnicodeDecodeError as exc:
        raise SystemExit(f"Alert file must be UTF-8 text: {path_str}") from exc
    except OSError as exc:
        raise SystemExit(f"Could not read alert file {path_str}: {exc}") from exc

    if Path(path_str).suffix.lower() == ".json":
        return parse_payload_text(raw_text, path_str)

    # For .md, .txt, and everything else: try to pull a fenced JSON block
    match = re.search(r"```json\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
    if match:
        return parse_payload_text(match.group(1), path_str)

    # No structured JSON — let the agent interpret the raw content
    return {"raw_text": raw_text}


def load_stdin() -> dict[str, Any]:
    """Read a JSON payload from stdin."""
    if sys.stdin.isatty():
        raise SystemExit(
            "No alert input provided on stdin. Use --interactive, --input <file>, or --input-json."
        )
    return parse_payload_text(sys.stdin.read(), "stdin")


def load_interactive() -> dict[str, Any]:
    """Prompt the user to paste an alert payload."""
    print("Paste the alert JSON payload, then press Ctrl-D when finished.", file=sys.stderr)
    raw_text = sys.stdin.read()
    if not raw_text.strip():
        raise SystemExit("No alert JSON was provided in interactive mode.")
    return parse_payload_text(raw_text, "interactive input")


def load_payload(
    input_path: str | None,
    input_json: str | None,
    interactive: bool,
) -> dict[str, Any]:
    """Dispatch to the right loader based on what the user passed."""
    if input_json:
        return parse_payload_text(input_json, "--input-json")
    if interactive:
        return load_interactive()
    if input_path == "-":
        return load_stdin()
    if input_path:
        return load_file(input_path)
    if sys.stdin.isatty():
        from app.cli.investigate_input import prompt_for_input

        return prompt_for_input()
    return load_stdin()
