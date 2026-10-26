"""User-facing status copy for the Telegram gateway placeholder message.

Every status funnels through :func:`normalize_gateway_status`, which swaps the
legacy ``Working…`` placeholder (and empty text) for real copy.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from core.llm.shared.llm_retry import CREDIT_EXHAUSTED_MARKER

#: Returns a tool's own wording (display name, description) for status copy.
DescribeTool = Callable[[str], tuple[str, ...]]

INITIAL_STATUSES: tuple[str, ...] = (
    "🔍 On it — give me a moment…",
    "⚡ Digging in…",
    "🛠️ Checking your stack…",
    "📡 Pulling the latest signals…",
)

_WORKING_PLACEHOLDER = re.compile(r"working(\.{3}|…)?", re.IGNORECASE)


def initial_status_message() -> str:
    """Return a short, varied placeholder shown while the first turn starts."""
    return random.choice(INITIAL_STATUSES)


def normalize_gateway_status(status: str) -> str:
    """Swap empty or legacy ``Working…`` copy for a real status line."""
    stripped = status.strip()
    if not stripped or _WORKING_PLACEHOLDER.fullmatch(stripped):
        return initial_status_message()
    return status


def chat_status_headline(status: str) -> str:
    """One row for a shared chat.

    Later rows are the copyable argument, and that argument stays on the
    shell. A channel preview never receives it.
    """
    normalized = normalize_gateway_status(status)
    for row in normalized.splitlines():
        if line := " ".join(row.split()):
            return line
    return normalized


_GENERIC_ERROR = "Something went wrong handling that request. Please try again."

# Shown when a turn streams no status at all, so the placeholder is not left blank.
EMPTY_RESPONSE_MESSAGE = "I didn't have anything to add for that."


def user_facing_error_message(detail: str) -> str:
    """Safe chat copy for an internal error string.

    External Slack/Telegram users must never see raw exception detail; it is
    logged server-side instead. Known-actionable conditions get specific
    guidance, everything else a generic message.
    """
    if CREDIT_EXHAUSTED_MARKER in detail:
        return (
            "The assistant is temporarily unavailable: LLM credits are exhausted. "
            "Run `opensre auth login <provider>` to re-authenticate or switch providers."
        )
    return _GENERIC_ERROR


def status_from_response_label(label: str) -> str:
    """Map harness response labels (e.g. ``assistant``) to Telegram status text."""
    label = label.strip()
    if not label or _WORKING_PLACEHOLDER.fullmatch(label):
        return initial_status_message()
    if label.lower() == "assistant":
        return "💬 Composing your reply…"
    return f"✨ {label}…"


def status_from_tool_start(
    tool_name: str,
    tool_input: Any = None,
    *,
    describe: DescribeTool | None = None,
) -> str:
    """Build ``⏳ label…`` status while an action tool runs.

    The argument sits on the following row. Together they may wrap to three
    rows; neither the label nor the argument is shortened, because the argument
    is what a person copies out of the terminal.

    ``describe`` supplies the tool's own wording; the host is handed it rather
    than reading a registry, so this module stays below the tool tier. Without
    one the label is the humanized tool name.
    """
    name = tool_name.strip()
    if not name:
        return initial_status_message()
    candidates = describe(name) if describe is not None else ()
    label = f"⏳ {_tool_label(name, candidates)}…"
    hint = _input_hint(tool_input).lstrip()
    if not hint:
        return label
    return f"{label}\n{hint}"


@lru_cache(maxsize=256)
def _tool_label(tool_name: str, candidates: tuple[str, ...]) -> str:
    """First clause of the tool's own wording, else its humanized name."""
    for text in (*candidates, tool_name.replace("_", " ")):
        clause = re.split(r"\.\s| — | - |; ", " ".join(text.split()), maxsplit=1)[0]
        if clause := clause.rstrip("."):
            return clause
    return tool_name


def _input_hint(tool_input: Any) -> str:
    """First meaningful argument value, as an inline ``(hint)``.

    The hint is what a person copies (a path, a command, a skill name), so it
    is not cut down to a preview.
    """
    if not isinstance(tool_input, dict):
        return ""
    for value in tool_input.values():
        items = value if isinstance(value, list) else [value] if isinstance(value, str) else []
        text = " ".join(part for item in items if (part := " ".join(str(item).split())))
        if text:
            return f" ({text})"
    return ""


__all__ = [
    "DescribeTool",
    "EMPTY_RESPONSE_MESSAGE",
    "chat_status_headline",
    "initial_status_message",
    "normalize_gateway_status",
    "status_from_response_label",
    "status_from_tool_start",
    "user_facing_error_message",
]
