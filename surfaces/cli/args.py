"""CLI argument helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(data: Any, path: str | None) -> None:
    """Write JSON to file or stdout."""
    if path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    else:
        print(json.dumps(data, indent=2))
