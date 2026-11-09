"""Thread-safe JSONL persistence for gateway response feedback."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("gateway")

_WRITE_LOCK = threading.Lock()


def append_feedback_entry(entry: dict[str, Any], *, path: Path) -> bool:
    """Append one feedback entry, returning whether persistence succeeded."""
    try:
        with _WRITE_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError):
        logger.warning("[gateway] feedback serialization/write failed path=%s", path, exc_info=True)
        return False
    return True


__all__ = ["append_feedback_entry"]
