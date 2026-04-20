"""CLI argument helpers for the incident resolution agent."""

import argparse
import json
from pathlib import Path
from typing import Any

from app.cli.constants import ALERT_TEMPLATE_CHOICES


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Run an RCA investigation against a user-provided alert payload."
    )
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--input",
        "-i",
        default=None,
        help="Path to an alert file (.json, .md, .txt, …). Use - to read from stdin.",
    )
    input_group.add_argument(
        "--input-json",
        default=None,
        help="Inline alert JSON string.",
    )
    input_group.add_argument(
        "--interactive",
        action="store_true",
        help="Paste an alert JSON payload into the terminal.",
    )
    input_group.add_argument(
        "--print-template",
        choices=ALERT_TEMPLATE_CHOICES,
        default=None,
        help="Print a starter alert JSON template and exit.",
    )
    parser.add_argument("--output", "-o", default=None, help="Output JSON file (default: stdout)")
    return parser.parse_args(argv)


def write_json(data: Any, path: str | None) -> None:
    """Write JSON to file or stdout."""
    if path:
        Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    else:
        print(json.dumps(data, indent=2))
