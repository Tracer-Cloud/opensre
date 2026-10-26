"""Row compaction and field truncation for groundcover tool responses."""

from __future__ import annotations

from typing import Any

# Safety cap applied to rows we put into the prompt envelope, independent of the
# gcQL ``| limit`` the server enforced. Keeps noisy results bounded.
_ENVELOPE_ROW_CAP = 100
_MAX_FIELD_CHARS = 1000


def _truncate_value(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_FIELD_CHARS:
        return value[: _MAX_FIELD_CHARS - 3] + "..."
    return value


def compact_rows(rows: list[Any], limit: int = _ENVELOPE_ROW_CAP) -> tuple[list[Any], bool]:
    """Cap row count and truncate long string fields. Returns (rows, capped)."""
    capped = len(rows) > limit
    out: list[Any] = []
    for row in rows[:limit]:
        if isinstance(row, dict):
            out.append({k: _truncate_value(v) for k, v in row.items()})
        else:
            out.append(_truncate_value(row))
    return out, capped
