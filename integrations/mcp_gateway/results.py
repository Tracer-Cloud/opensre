"""Credential-safe, bounded copies of model-visible gateway tool results."""

from __future__ import annotations

import json
from typing import Any

from infrastructure.observability.trace.redaction import redact_sensitive
from infrastructure.text import json_size_up_to
from integrations.mcp_gateway.redaction import redacted_text_preview, scrub_configured_token

_MAX_RESULT_CHARS = 60_000
_TEXT_PREVIEW_CHARS = 4_000
_MAX_PREVIEW_DEPTH = 16


def _bounded_preview(value: Any, auth_token: str) -> Any:
    """Copy only a small prefix of remote JSON before applying recursive redaction."""
    remaining = _TEXT_PREVIEW_CHARS

    def visit(item: Any, depth: int) -> Any:
        nonlocal remaining
        if remaining <= 0 or depth > _MAX_PREVIEW_DEPTH:
            return "[omitted]"
        remaining -= 2
        if isinstance(item, str):
            budget = max(1, remaining)
            remaining -= min(len(item), budget)
            return redacted_text_preview(item, auth_token, max_chars=budget)
        if isinstance(item, dict):
            copied: dict[str, Any] = {}
            for key, child in item.items():
                if remaining <= 0:
                    break
                if not isinstance(key, str) or len(key) > 256:
                    remaining -= 32
                    copied[f"omitted_field_{len(copied)}"] = "[oversized field omitted]"
                    continue
                remaining -= len(key) + 4
                copied[key] = visit(child, depth + 1)
            return copied
        if isinstance(item, (list, tuple)):
            items = []
            for child in item:
                if remaining <= 0:
                    break
                items.append(visit(child, depth + 1))
            return items
        size = json_size_up_to(item, max(1, remaining))
        if size is None or size > remaining:
            remaining = 0
            return "[omitted]"
        remaining -= size
        return item

    return redact_sensitive(scrub_configured_token(visit(value, 0), auth_token))


def _result_preview(payload: dict[str, object], auth_token: str) -> str:
    """Keep text or a bounded, explicitly incomplete preview of structured data."""
    text = payload.get("text")
    if isinstance(text, str) and text[:_TEXT_PREVIEW_CHARS].strip():
        return redacted_text_preview(text, auth_token, max_chars=_TEXT_PREVIEW_CHARS)
    structured = payload.get("structured_content") or payload.get("content")
    if not structured:
        return ""
    parts: list[str] = []
    remaining = _TEXT_PREVIEW_CHARS
    try:
        for chunk in json.JSONEncoder(ensure_ascii=True).iterencode(
            _bounded_preview(structured, auth_token)
        ):
            part = chunk[:remaining]
            parts.append(part)
            remaining -= len(part)
            if not remaining:
                break
    except (TypeError, ValueError, RecursionError):
        return ""
    return "Structured data preview (incomplete):\n" + "".join(parts)


def safe_tool_result(payload: dict[str, object], *, auth_token: str) -> dict[str, Any]:
    """Redact every output path, preserving small results and marking omitted data."""
    size = json_size_up_to(payload, _MAX_RESULT_CHARS)
    if size is not None and size <= _MAX_RESULT_CHARS:
        safe: dict[str, Any] = redact_sensitive(scrub_configured_token(payload, auth_token))
        safe_size = json_size_up_to(safe, _MAX_RESULT_CHARS)
        if safe_size is not None and safe_size <= _MAX_RESULT_CHARS:
            return safe
    summary = _result_preview(payload, auth_token)
    tool = payload.get("tool")
    result = {
        "source": "mcp_gateway",
        "available": payload.get("available") is not False,
        "tool": redacted_text_preview(tool, auth_token, max_chars=256)
        if isinstance(tool, str)
        else "",
        "arguments": {},
        "text": summary + "\n[truncated: complete remote result omitted]",
        "structured_content": None,
        "content": [],
        "truncated": True,
        "original_serialized_chars": size,
        "original_size_is_lower_bound": size is not None and size > _MAX_RESULT_CHARS,
        "is_error": bool(payload.get("is_error")),
        "notes": "Do not repeat a completed mutation to retrieve its full result. Narrow read queries instead.",
    }
    for key in ("error", "error_kind"):
        value = payload.get(key)
        if isinstance(value, str):
            result[key] = redacted_text_preview(value, auth_token, max_chars=1_000)
    return result
