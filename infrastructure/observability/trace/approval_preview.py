"""Bounded approval previews that preserve argument fields and redact secrets."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from infrastructure.observability.trace.redaction import redact_sensitive
from infrastructure.text import json_size_up_to

_TRUNCATED = "… [truncated]"
_MIN_VALUE_CHARS = 16
_TOO_MANY_FIELDS = "Too many argument fields to review safely; reduce the payload."
_MAX_REVIEW_CHARS = 64_000
_TOO_LARGE = "Arguments exceed the complete review limit; reduce the payload."


@dataclass(frozen=True)
class ApprovalPreview:
    """Bounded summary and complete redacted evidence required for authorization."""

    text: str
    fields_visible: bool
    full_text: str = ""

    @property
    def truncated(self) -> bool:
        """Whether the summary alone is insufficient to authorize the call."""
        return bool(self.full_text and self.text != self.full_text)


def _map_values(value: Any, transform: Callable[[str], str]) -> Any:
    if isinstance(value, dict):
        return {key: _map_values(item, transform) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_map_values(item, transform) for item in value]
    if isinstance(value, str):
        return transform(value)
    return value


def _shorten(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    keep = max(0, limit - len(_TRUNCATED))
    head = (keep + 1) // 2
    tail = keep // 2
    return value[:head] + _TRUNCATED + (value[-tail:] if tail else "")


def format_approval_preview(
    value: Any,
    *,
    max_chars: int,
    scrub_text: Callable[[str], str] | None = None,
) -> ApprovalPreview:
    """Shorten individual values, never drop fields to satisfy the display budget."""
    size = json_size_up_to(value, _MAX_REVIEW_CHARS)
    if size is not None and size > _MAX_REVIEW_CHARS:
        return ApprovalPreview(_TOO_LARGE[:max_chars], fields_visible=False)
    try:
        safe = redact_sensitive(value)
        if scrub_text is not None:
            safe = _map_values(safe, scrub_text)
        text = json.dumps(safe, ensure_ascii=True).replace("`", "\\u0060")
        if len(text) > _MAX_REVIEW_CHARS:
            return ApprovalPreview(_TOO_LARGE[:max_chars], fields_visible=False)
        if len(text) <= max_chars:
            return ApprovalPreview(text, fields_visible=True, full_text=text)
        full_text = text
        limit = max(_MIN_VALUE_CHARS, max_chars // 2)
        while True:
            compact = _map_values(safe, partial(_shorten, limit=limit))
            text = json.dumps(compact, ensure_ascii=True).replace("`", "\\u0060")
            if len(text) <= max_chars:
                return ApprovalPreview(text, fields_visible=True, full_text=full_text)
            if limit == _MIN_VALUE_CHARS:
                break
            limit = max(_MIN_VALUE_CHARS, limit // 2)
    except (TypeError, ValueError, RecursionError):
        # Non-JSON inputs cannot be faithfully represented for authorization.
        pass
    return ApprovalPreview(_TOO_MANY_FIELDS[:max_chars], fields_visible=False)
