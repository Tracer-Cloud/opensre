"""Turn tool-result and reply text as it is shown in the transcript.

Formats a tool result into the user-facing line the transcript shows, caps
verbose output to a short head, and hides raw data blobs the reply already
summarizes. Kept apart from ``action_driver`` so the turn driver stays about
driving the turn and this module is the one place display text is shaped.
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.llm.types import ToolCall
from infrastructure.terminal.peek import (
    DISPLAY_OUTPUT_MAX_CHARS,
    DISPLAY_OUTPUT_MAX_LINES,
    cap_output_for_display,
    format_view_all_marker,
)
from infrastructure.text import is_data_blob

# Tools whose result the host already rendered to the console; their payload is
# not re-shown in the transcript. A tool outside this set says so per call with
# ``rendered_in_shell`` in its payload (it paints only on a terminal surface).
_HOST_RENDERED_TOOL_NAMES: frozenset[str] = frozenset(
    {"ask_user_choice", "skill_view", "update_plan"}
)


def _host_rendered(tool_call: ToolCall, tool_result: Any) -> bool:
    """True when the console already shows this result, so it is not re-shown."""
    if tool_call.name in _HOST_RENDERED_TOOL_NAMES:
        return True
    details = getattr(tool_result, "details", None)
    return isinstance(details, dict) and details.get("rendered_in_shell") is True


_EXPAND_MARKER_RE = re.compile(r"^… \d+ more, Ctrl\+O to view$")
_PLAN_SNAPSHOT_RE = re.compile(r"Plan\s*[·.]\s*\d+\s*/\s*\d+(?:\s*[✓●○][^✓●○\n]*)*")


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return json.dumps(content, default=str)
    return str(content)


def strip_plan_snapshots(text: str) -> str:
    """Remove ``Plan · n/m`` checklist snapshots the model restates in its reply.

    The plan lives in the pinned overlay, so echoing it — let alone every
    historical step-completion state — is a redundant wall. Prose (``-``/``•``
    bullets, sentences) is untouched."""
    if "Plan" not in text:
        return text
    cleaned = _PLAN_SNAPSHOT_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def looks_like_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return False
    try:
        json.loads(stripped)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return True


def _is_output_truncation_marker(line: str) -> bool:
    return line == format_view_all_marker() or bool(_EXPAND_MARKER_RE.fullmatch(line))


def split_output_truncation_markers(text: str) -> tuple[str, str]:
    """Peel the trailing expand marker from a capped preview.

    Returns ``(body, marker)``. *marker* is the single Droid-style line
    (``… N more, Ctrl+O to view`` or ``Ctrl+O to view all``), or empty.
    """
    lines = text.split("\n")
    cut = len(lines)
    while cut and _is_output_truncation_marker(lines[cut - 1]):
        cut -= 1
    return "\n".join(lines[:cut]), "\n".join(lines[cut:])


def cap_for_display(text: str) -> str:
    """Cap verbose tool output for the console so a large result cannot flood the
    transcript. The model and persisted history keep the full text; only the
    user-facing preview is truncated to a short head (Droid-style).
    """
    preview, _full = cap_output_for_display(text)
    return preview


def _user_facing_tool_text(text: str) -> str:
    """Return *text* for the transcript, or ``""`` when it is a data blob."""
    stripped = text.strip()
    if not stripped or is_data_blob(stripped):
        return ""
    return stripped


def _visible_stdout(stdout: str) -> str:
    """Plain-text stdout is shown as-is; a JSON payload is hidden.

    A ``gh api`` / structured response is data the reply already summarizes, so
    dumping it into the transcript only adds a wall that reads as unattached to
    the command. Hide the blob and let the summary carry the answer — the way
    Claude Code / Droid keep tool results out of the reply prose. Plain-text
    output (logs, a short listing) is still shown, capped later at display time.
    """
    return _user_facing_tool_text(stdout)


def format_generic_tool_payload(tool_call: ToolCall, tool_result: Any) -> str:
    """Build a user-visible summary for one non-self-recording tool result."""
    if _host_rendered(tool_call, tool_result) and not getattr(tool_result, "is_error", False):
        return ""
    preferred_response = preferred_tool_response_text(tool_result)
    if preferred_response:
        return _user_facing_tool_text(preferred_response)
    details = getattr(tool_result, "details", None)
    if isinstance(details, dict):
        summary = details.get("summary")
        if isinstance(summary, str) and summary.strip():
            # gh ``summary`` for ``api`` used to be a sliced JSON string — hide
            # that the same way as raw stdout so the transcript stays prose.
            return _user_facing_tool_text(summary)
        stdout = details.get("stdout")
        if details.get("ok") and isinstance(stdout, str) and stdout.strip():
            return _visible_stdout(stdout)
        error = details.get("error")
        if error:
            return str(error).strip()
    if getattr(tool_result, "is_error", False):
        return ""
    content = _content_to_text(getattr(tool_result, "content", "")).strip()
    if not content:
        return ""
    # Prefer a nested summary when the tool returned a JSON object payload.
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        response_text = parsed.get("response_text")
        if isinstance(response_text, str) and response_text.strip():
            return _user_facing_tool_text(response_text)
        summary = parsed.get("summary")
        if isinstance(summary, str) and summary.strip():
            return _user_facing_tool_text(summary)
        if parsed.get("ok") and isinstance(parsed.get("stdout"), str) and parsed["stdout"].strip():
            return _visible_stdout(str(parsed["stdout"]))
        if parsed.get("error"):
            return str(parsed["error"]).strip()
    if isinstance(parsed, (dict, list)):
        # An opaque JSON payload (no user-facing field — e.g. a raw ``gh api``
        # response) is for the model, not the transcript: the reply summarizes
        # it. Hide it rather than dump a wall of data unattached to the command.
        return ""
    # A truncated / fragmentary JSON blob (a capped ``gh api`` response) that
    # failed to parse — hide it, same as valid JSON.
    if is_data_blob(content):
        return ""
    # Non-JSON content is the tool's real text output; show it under the name.
    return f"{tool_call.name} result: {content}"


def preferred_tool_response_text(tool_result: Any) -> str:
    details = getattr(tool_result, "details", None)
    if isinstance(details, dict):
        response_text = details.get("response_text")
        if isinstance(response_text, str) and response_text.strip():
            return _user_facing_tool_text(response_text)
    content = _content_to_text(getattr(tool_result, "content", "")).strip()
    if not content:
        return ""
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(parsed, dict):
        return ""
    response_text = parsed.get("response_text")
    if not isinstance(response_text, str):
        return ""
    return _user_facing_tool_text(response_text)


__all__ = [
    "DISPLAY_OUTPUT_MAX_CHARS",
    "DISPLAY_OUTPUT_MAX_LINES",
    "cap_for_display",
    "format_generic_tool_payload",
    "looks_like_json",
    "preferred_tool_response_text",
    "split_output_truncation_markers",
    "strip_plan_snapshots",
]
