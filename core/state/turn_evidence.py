"""Structured evidence of completed turns: what the agent did, not only what it said.

The text transcript (``MutableAgentState.messages``) keeps one ``(role, text)``
pair per message. A :class:`TurnEvidence` keeps the same turn as JSON-safe items
in order: the user's message, each assistant tool-call batch with its results,
and the final reply. A later turn replays those items instead of a paraphrase.
Items are bounded when the turn is recorded; the full tool output stays in the
session log.

Evidence is matched to transcript pairs by their text, newest first. A
transcript rewritten elsewhere (seeded from a chat thread, compacted, restored)
therefore degrades to plain text for the pairs it no longer matches instead of
replaying the wrong turn.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: The user's message for the turn (clean text, no per-turn context blocks).
ITEM_USER = "user"
#: One assistant message: text and the tool calls it requested, if any.
ITEM_ASSISTANT = "assistant"
#: The results of the preceding assistant message's tool calls, in call order.
ITEM_TOOL_RESULTS = "tool_results"

_ITEM_KINDS = frozenset({ITEM_USER, ITEM_ASSISTANT, ITEM_TOOL_RESULTS})
_TRUNCATION_MARKER = "\n…[{omitted} characters omitted]…\n"


def cap_text(text: str, max_chars: int) -> str:
    """Keep the head and tail of ``text`` within ``max_chars``, marking the cut.

    Head and tail both survive because tool output carries its shape at the top
    and its outcome (exit status, totals, the error) at the bottom.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    widest_marker = len(_TRUNCATION_MARKER.format(omitted=len(text)))
    keep = max(max_chars - widest_marker, 0)
    head = keep // 2
    tail = keep - head
    marker = _TRUNCATION_MARKER.format(omitted=len(text) - keep)
    return "".join((text[:head], marker, text[len(text) - tail :] if tail else ""))


@dataclass(frozen=True)
class TurnEvidence:
    """One completed turn as ordered, bounded, JSON-safe items."""

    user_text: str
    assistant_text: str
    items: tuple[Mapping[str, Any], ...] = ()

    @property
    def has_tool_activity(self) -> bool:
        """True when the turn ran at least one tool."""
        return any(item.get("kind") == ITEM_TOOL_RESULTS for item in self.items)

    def char_count(self) -> int:
        """Characters the turn adds when replayed (text, arguments and results)."""
        total = 0
        for item in self.items:
            total += len(str(item.get("text") or ""))
            for call in item.get("tool_calls") or ():
                total += len(str(call.get("name") or "")) + len(str(call.get("input") or ""))
            for result in item.get("results") or ():
                total += len(str(result.get("content") or ""))
        return total

    def to_json(self) -> dict[str, Any]:
        """The record persisted in the session log."""
        return {
            "user_text": self.user_text,
            "assistant_text": self.assistant_text,
            "items": [dict(item) for item in self.items],
        }

    @classmethod
    def from_json(cls, payload: Any) -> TurnEvidence | None:
        """Rebuild a persisted record; ``None`` when it is not one."""
        if not isinstance(payload, Mapping):
            return None
        user_text = payload.get("user_text")
        assistant_text = payload.get("assistant_text")
        raw_items = payload.get("items")
        if not isinstance(user_text, str) or not isinstance(assistant_text, str):
            return None
        if not isinstance(raw_items, list):
            return None
        items = tuple(
            dict(item)
            for item in raw_items
            if isinstance(item, Mapping) and item.get("kind") in _ITEM_KINDS
        )
        return cls(user_text=user_text, assistant_text=assistant_text, items=items)


def match_turn_evidence(
    messages: Sequence[tuple[str, str]],
    evidence: Sequence[TurnEvidence],
) -> dict[int, TurnEvidence]:
    """Map the index of each user message in ``messages`` to its turn's evidence.

    Walks user/assistant pairs newest first and pairs each with the newest
    unused evidence for the same reply that is older than the evidence matched
    to the pair after it, so order is preserved and two turns with the same
    reply cannot cross over. The reply identifies a turn; the user text breaks
    ties but is not required, because a restored session keeps the message as
    typed ("yes") while the live transcript recorded its expansion. Pairs
    without a match are left out and replay as text.
    """
    if not messages or not evidence:
        return {}
    by_reply: dict[str, list[int]] = {}
    for index, record in enumerate(evidence):
        by_reply.setdefault(record.assistant_text, []).append(index)
    matched: dict[int, TurnEvidence] = {}
    limit = len(evidence)
    position = len(messages) - 1
    while position >= 1:
        user_role, user_text = messages[position - 1]
        assistant_role, assistant_text = messages[position]
        if user_role != "user" or assistant_role != "assistant":
            position -= 1
            continue
        candidates = [index for index in by_reply.get(assistant_text, ()) if index < limit]
        if candidates:
            exact = [index for index in candidates if evidence[index].user_text == user_text]
            limit = (exact or candidates)[-1]
            matched[position - 1] = evidence[limit]
        position -= 2
    return matched


__all__ = [
    "ITEM_ASSISTANT",
    "ITEM_TOOL_RESULTS",
    "ITEM_USER",
    "TurnEvidence",
    "cap_text",
    "match_turn_evidence",
]
