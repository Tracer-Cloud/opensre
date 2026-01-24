"""CLI utilities for the incident resolution agent."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    p = argparse.ArgumentParser(description="Run incident resolution agent.")
    p.add_argument("--input", "-i", default="-", help="Grafana alert JSON (- for stdin)")
    p.add_argument("--output", "-o", default=None, help="Output JSON file (default: stdout)")
    return p.parse_args(argv)


def write_json(data: Any, path: str | None) -> None:
    """Write JSON to file or stdout."""
    if path:
        Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    else:
        json.dump(data, sys.stdout, indent=2)
        sys.stdout.write("\n")
