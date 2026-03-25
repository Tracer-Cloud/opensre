"""CLI argument helpers for the incident resolution agent."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


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
        help="Path to an alert JSON file. Use - to read JSON from stdin.",
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
        choices=["generic", "datadog", "grafana"],
        default=None,
        help="Print a starter alert JSON template and exit.",
    )
    parser.add_argument(
        "--alert-name",
        default=None,
        help="Optional alert name override.",
    )
    parser.add_argument(
        "--pipeline-name",
        default=None,
        help="Optional pipeline or service name override.",
    )
    parser.add_argument(
        "--severity",
        default=None,
        help="Optional severity override.",
    )
    parser.add_argument("--output", "-o", default=None, help="Output JSON file (default: stdout)")
    return parser.parse_args(argv)


def write_json(data: Any, path: str | None) -> None:
    """Write JSON to file or stdout."""
    if path:
        Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    else:
        json.dump(data, sys.stdout, indent=2)
        sys.stdout.write("\n")
