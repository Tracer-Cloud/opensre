"""Bound JSON counting without serializing or copying payloads."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from enum import Enum
from typing import Any

_MAX_JSON_DEPTH = 64


def json_size_up_to(value: Any, limit: int) -> int | None:
    """Count ASCII JSON characters up to a budget; excess counts are lower bounds.

    Pydantic-style models are walked through their declared fields rather than
    dumped first, so callers can reject oversized remote objects without a
    payload-sized intermediate allocation.
    """
    size = 0

    def model_items(item: Any) -> list[tuple[str, Any]] | None:
        fields = getattr(type(item), "model_fields", None)
        if not isinstance(fields, Mapping):
            return None
        return [(str(name), getattr(item, name)) for name in fields]

    def visit(item: Any, depth: int) -> None:
        nonlocal size
        if size > limit:
            return
        if depth > _MAX_JSON_DEPTH:
            raise ValueError("JSON nesting budget exceeded")
        declared_items = model_items(item)
        if isinstance(item, str):
            if len(item) + 2 > limit - size:
                size += len(item) + 2
                return
            size += 2
            for character in item:
                code = ord(character)
                if character in '\\"\b\f\n\r\t':
                    size += 2
                elif code < 32 or code >= 127:
                    size += 12 if code > 65535 else 6
                else:
                    size += 1
                if size > limit:
                    return
        elif item is None:
            size += 4
        elif isinstance(item, bool):
            size += 4 if item else 5
        elif isinstance(item, int):
            size += limit + 1 if item.bit_length() > limit * 4 else len(str(item))
        elif isinstance(item, float):
            size += (9 if item < 0 else 8) if math.isinf(item) else len(repr(item))
        elif isinstance(item, Enum):
            visit(item.value, depth)
        elif isinstance(item, dict) or declared_items is not None:
            size += 2
            entries: Iterable[tuple[Any, Any]]
            if isinstance(item, dict):
                entries = item.items()
            else:
                assert declared_items is not None
                entries = declared_items
            for index, (key, child) in enumerate(entries):
                if size > limit:
                    break
                if not isinstance(key, str):
                    raise TypeError("JSON object keys must be strings")
                size += 2 if index else 0
                visit(key, depth + 1)
                size += 2
                visit(child, depth + 1)
        elif isinstance(item, (list, tuple)):
            size += 2
            for index, child in enumerate(item):
                if size > limit:
                    break
                size += 2 if index else 0
                visit(child, depth + 1)
        else:
            raise TypeError("Value is not JSON data")

    try:
        visit(value, 0)
    except (AttributeError, TypeError, ValueError, RecursionError):
        return None
    return size


__all__ = ["json_size_up_to"]
