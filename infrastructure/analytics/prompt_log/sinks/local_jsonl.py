"""Local JSONL sink for prompt/response records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DEFAULT_MAX_BYTES = 50 * 1024 * 1024


def append_prompt_log_record(
    *,
    path: Path,
    record: dict[str, Any],
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_if_needed(path, max_bytes=max_bytes)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _rotate_if_needed(path: Path, *, max_bytes: int) -> None:
    if max_bytes <= 0 or not path.exists():
        return
    if path.stat().st_size <= max_bytes:
        return
    backup = path.with_name(path.name + ".1")
    if backup.exists():
        backup.unlink()
    path.rename(backup)
