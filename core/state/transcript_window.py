"""Transcript window compaction: overflow becomes a running summary message.

When a conversation transcript exceeds its message window, the oldest
messages are compacted into a single leading ``Session summary:`` assistant
message, so facts stated early in a session stay visible. This enforces the
message-count window; the char-threshold session compaction in the turns
layer is a separate mechanism that shares the same summary-message format.
"""

from __future__ import annotations

from collections.abc import Sequence

SESSION_SUMMARY_PREFIX = "Session summary:\n"
SUMMARY_MAX_CHARS = 6_000
_SUMMARY_LINE_MAX_CHARS = 700
_SUMMARY_GAP_MARKER = "\n…[earlier compacted context omitted]…\n"
#: Share of a full summary kept from its start; the rest comes from its end.
_SUMMARY_HEAD_SHARE = 0.4


def format_messages_for_summary(
    messages: Sequence[tuple[str, str]],
    *,
    max_line_chars: int = _SUMMARY_LINE_MAX_CHARS,
) -> str:
    """Render messages as compact ``- role: text`` lines, one per message."""
    lines: list[str] = []
    for role, text in messages:
        compact = " ".join(str(text).split())
        if len(compact) > max_line_chars:
            compact = compact[: max_line_chars - 3] + "..."
        lines.append(f"- {role}: {compact}")
    return "\n".join(lines)


def compact_messages_to_window(
    messages: Sequence[tuple[str, str]],
    *,
    max_messages: int,
    summary_max_chars: int = SUMMARY_MAX_CHARS,
) -> list[tuple[str, str]]:
    """Return ``messages`` within the window, compacting overflow into a summary.

    Within the window the list is returned unchanged. On overflow, the most
    recent messages are kept verbatim (an even count, so user/assistant turn
    pairing survives) and everything older is summarized into one leading
    ``Session summary:`` assistant message. An existing leading summary is
    merged, never stacked; a full summary keeps its start and its end, so the
    earliest facts and the most recently compacted turns both survive.
    """
    if max_messages < 1:
        raise ValueError("max_messages must be >= 1")
    entries = list(messages)
    if len(entries) <= max_messages:
        return entries

    keep_count = max_messages - 1
    keep_count -= keep_count % 2
    kept = entries[-keep_count:] if keep_count else []
    overflow = entries[: len(entries) - keep_count]

    prior = ""
    if overflow and is_summary_message(overflow[0]):
        prior = summary_text(overflow[0])
        overflow = overflow[1:]

    merged = merge_summary_texts(
        prior, format_messages_for_summary(overflow), max_chars=summary_max_chars
    )
    return [("assistant", f"{SESSION_SUMMARY_PREFIX}{merged}"), *kept]


def merge_summary_texts(prior: str, addition: str, *, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """Merge summary bodies; over the cap, keep the oldest and the newest context.

    The start holds what the session is about and the facts stated first; the
    end holds what was compacted last. Cutting only the end froze a full
    summary, so every turn that left the window afterwards was dropped unread.
    """
    merged = "\n".join(part for part in (prior, addition) if part)
    if len(merged) <= max_chars:
        return merged
    keep = max(max_chars - len(_SUMMARY_GAP_MARKER), 0)
    if keep == 0:
        return merged[:max_chars]
    head = int(keep * _SUMMARY_HEAD_SHARE)
    tail = keep - head
    return "".join((merged[:head], _SUMMARY_GAP_MARKER, merged[len(merged) - tail :]))


def is_summary_message(entry: tuple[str, str]) -> bool:
    """True when ``entry`` is a session-summary assistant message."""
    role, content = entry
    return (
        role == "assistant"
        and isinstance(content, str)
        and content.startswith(SESSION_SUMMARY_PREFIX)
    )


def summary_text(entry: tuple[str, str]) -> str:
    """Return the summary body of a session-summary message."""
    return entry[1][len(SESSION_SUMMARY_PREFIX) :]


__all__ = [
    "SUMMARY_MAX_CHARS",
    "SESSION_SUMMARY_PREFIX",
    "compact_messages_to_window",
    "format_messages_for_summary",
    "is_summary_message",
    "merge_summary_texts",
    "summary_text",
]
