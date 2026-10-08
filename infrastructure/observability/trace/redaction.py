"""Helpers for safe tool-call tracing in CLI and reports."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from infrastructure.safety.secret_redaction import redact_text

_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|passphrase|credential|authorization|"
    r"auth[_-]?header|private[_-]?key|signing[_-]?key|seed[_-]?phrase|mnemonic)",
    re.IGNORECASE,
)
_RUNTIME_KEY_RE = re.compile(r"(^_|backend$|_backend$)", re.IGNORECASE)

_REDACTED_PLACEHOLDER = "[redacted]"
_RUNTIME_OBJECT_PLACEHOLDER = "[runtime object]"

#: Default bound for pretty-printed JSON previews in the terminal.
DEFAULT_JSON_PREVIEW_MAX_CHARS = 4000
#: Default bound for tool-result previews inside report lines.
DEFAULT_TOOL_TRACE_OUTPUT_MAX_CHARS = 1200
#: Bound for tool-argument previews (kept shorter than output).
_TOOL_TRACE_ARGS_MAX_CHARS = 500
#: Indent width for ``json.dumps`` previews.
_JSON_PREVIEW_INDENT = 2
#: Marker appended when a preview is truncated to ``max_chars``.
_JSON_TRUNCATION_SUFFIX = "\n... [truncated]"
#: ``loop_iteration`` value meaning "seed / pre-loop" tool evidence.
_SEED_LOOP_ITERATION = -1


@dataclass(frozen=True, slots=True)
class RedactedToolView:
    """Frozen holder for one deep-copied redacted tool input/output.

    Security contract:
    - ``tool_input`` / ``output`` are produced by :func:`redact_sensitive` —
      deep copies with credential/runtime keys replaced. They are **not**
      aliases of the raw tool payload.
    - Share this view across tracker, events, ``evidence_entries``, and
      ``tool_outputs`` so each sink sees the same redacted tree (one walk).
    - Treat nested dicts/lists as **read-only**. The dataclass is frozen, but
      nested containers are ordinary copies and must not be mutated in place.
    - Stage logic that needs the unredacted tool result still reads
      ``evidence[tool_name]`` (raw) — that path is unchanged and intentional.
    """

    tool_input: Any
    output: Any | None = None


def redact_sensitive(value: Any) -> Any:
    """Return a deep copy of ``value`` with credentials and runtime objects hidden.

    Strings are scrubbed before serialization; other scalars are unchanged. Nested dict/list/
    tuple values are walked once into new containers — never aliases of
    ``value``'s nested objects.
    """
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            safe_key = redact_text(key_str)
            unique_key = safe_key
            duplicate = 1
            while unique_key in redacted:
                duplicate += 1
                unique_key = f"{safe_key} #{duplicate}"
            if _SENSITIVE_KEY_RE.search(key_str):
                redacted[unique_key] = _REDACTED_PLACEHOLDER
            elif _RUNTIME_KEY_RE.search(key_str):
                redacted[unique_key] = _RUNTIME_OBJECT_PLACEHOLDER
            else:
                redacted[unique_key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_tool_view(tool_input: Any, output: Any | None = None) -> RedactedToolView:
    """Deep-copy-redact tool input (and optional output) once for shared sinks.

    Does **not** skip redaction — it runs :func:`redact_sensitive` fully, then
    lets callers reuse that one copy instead of walking the same tree 3–5×.
    """
    return RedactedToolView(
        tool_input=redact_sensitive(tool_input),
        output=None if output is None else redact_sensitive(output),
    )


def format_json_preview(
    value: Any, *, max_chars: int | None = DEFAULT_JSON_PREVIEW_MAX_CHARS
) -> str:
    """Pretty-print a redacted JSON-ish value, bounded for terminal output."""
    redacted = redact_sensitive(value)
    try:
        text = json.dumps(redacted, indent=_JSON_PREVIEW_INDENT, default=str)
    except TypeError:
        text = str(redacted)
    if max_chars is None or len(text) <= max_chars:
        return text
    keep = max(0, max_chars - len(_JSON_TRUNCATION_SUFFIX))
    return text[:keep].rstrip() + _JSON_TRUNCATION_SUFFIX


def format_tool_trace_entry(
    entry: dict[str, Any], *, max_output_chars: int = DEFAULT_TOOL_TRACE_OUTPUT_MAX_CHARS
) -> str:
    """Format one evidence entry as a compact report line."""
    tool_name = str(entry.get("tool_name") or entry.get("key") or "tool")
    loop = entry.get("loop_iteration")
    loop_label = "seed" if loop == _SEED_LOOP_ITERATION else f"iteration {loop}"
    args = format_json_preview(entry.get("tool_args") or {}, max_chars=_TOOL_TRACE_ARGS_MAX_CHARS)
    output = format_json_preview(entry.get("data"), max_chars=max_output_chars)
    return f"- `{tool_name}` ({loop_label})\n  input: `{_one_line(args)}`\n  output: `{_one_line(output)}`"


def _one_line(value: str) -> str:
    return " ".join(value.split())


__all__ = [
    "DEFAULT_JSON_PREVIEW_MAX_CHARS",
    "DEFAULT_TOOL_TRACE_OUTPUT_MAX_CHARS",
    "RedactedToolView",
    "format_json_preview",
    "format_tool_trace_entry",
    "redact_sensitive",
    "redact_tool_view",
]
