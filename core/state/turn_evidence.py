"""Structured evidence of completed turns: what the agent did, not only what it said.

The text transcript (``MutableAgentState.messages``) keeps one ``(role, text)``
pair per message. A :class:`TurnEvidence` keeps the same turn as JSON-safe items
in order: the user's message, each assistant tool-call batch with its results,
and the final reply. A later turn replays those items instead of a paraphrase.
Items are bounded when the turn is recorded; audit records are independently
bounded and do not provide a lossless output archive.

Evidence is matched to transcript pairs by their text, newest first. A
transcript rewritten elsewhere (seeded from a chat thread, compacted, restored)
therefore degrades to plain text for the pairs it no longer matches instead of
replaying the wrong turn.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: The user's message for the turn: ``text`` is the clean text; ``sent`` (when
#: present) is the message exactly as the model received it, wrapped with the
#: turn's context, which replay sends again byte for byte; ``context_digest``
#: (when present) fingerprints the session-context section that message carried.
ITEM_USER = "user"
#: Key of an ``ITEM_USER`` item holding the message exactly as the model received it.
USER_SENT_KEY = "sent"
#: Key of an ``ITEM_USER`` item fingerprinting the session context its message carried.
USER_CONTEXT_DIGEST_KEY = "context_digest"
#: One assistant message: text and the tool calls it requested, if any.
ITEM_ASSISTANT = "assistant"
#: Key of an ``ITEM_ASSISTANT`` item holding the provider's own message for it,
#: exactly as the model sent it (reasoning included): ``{"binding": <client
#: binding>, "message": <JSON-safe message>, "recorded_at": <Unix seconds>}``.
#: Only a client with the same binding resends it; any other rebuilds the
#: message from its text and calls.
ASSISTANT_PROVIDER_KEY = "provider"
#: The results of the preceding assistant message's tool calls, in call order.
ITEM_TOOL_RESULTS = "tool_results"

_ITEM_KINDS = frozenset({ITEM_USER, ITEM_ASSISTANT, ITEM_TOOL_RESULTS})
_TRUNCATION_MARKER = "\n…[{omitted} characters omitted]…\n"


def provider_record(
    binding: str, message: Mapping[str, Any], *, recorded_at: float
) -> dict[str, Any] | None:
    """The ``ASSISTANT_PROVIDER_KEY`` value for ``message``; ``None`` when it is not plain JSON."""
    try:
        copied = json.loads(json.dumps(message, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError):
        return None
    if not binding or not isinstance(copied, dict):
        return None
    return {"binding": binding, "message": copied, "recorded_at": int(recorded_at)}


def recorded_provider_message(item: Mapping[str, Any]) -> tuple[dict[str, Any], str, int] | None:
    """The provider message, binding and record time an assistant item carries, if any."""
    record = item.get(ASSISTANT_PROVIDER_KEY)
    if not isinstance(record, Mapping):
        return None
    message = record.get("message")
    binding = record.get("binding")
    recorded_at = record.get("recorded_at")
    if not isinstance(message, Mapping) or not isinstance(binding, str) or not binding:
        return None
    if not isinstance(recorded_at, int) or isinstance(recorded_at, bool):
        return None
    return dict(message), binding, recorded_at


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
    #: The message as typed, when the transcript records an expansion of it
    #: (a bare "yes" becomes the offer it accepted). The session log keeps the
    #: typed form, so a restored transcript matches on this instead.
    typed_text: str = ""
    #: The tool list the conversation had been shown when the turn ended
    #: (:class:`~core.state.ShownTools`), so a session rebuilt from its log —
    #: as the gateway does for every message — keeps each tool in place.
    shown_tools: tuple[str, ...] = ()
    #: Of ``shown_tools``, the tools shown with their full description.
    full_tools: tuple[str, ...] = ()

    @property
    def has_tool_activity(self) -> bool:
        """True when the turn ran at least one tool."""
        return any(item.get("kind") == ITEM_TOOL_RESULTS for item in self.items)

    def char_count(self) -> int:
        """Characters the turn adds when replayed (text, arguments and results).

        An assistant message recorded with the provider's own message counts as
        that message, reasoning included, since the same client resends it whole.
        """
        total = 0
        for item in self.items:
            recorded = recorded_provider_message(item)
            if recorded is not None:
                total += len(json.dumps(recorded[0], ensure_ascii=False))
                continue
            total += len(str(item.get(USER_SENT_KEY) or item.get("text") or ""))
            for call in item.get("tool_calls") or ():
                total += len(str(call.get("name") or "")) + len(str(call.get("input") or ""))
            for result in item.get("results") or ():
                total += len(str(result.get("content") or ""))
        return total

    def to_json(self) -> dict[str, Any]:
        """The record persisted in the session log."""
        record: dict[str, Any] = {
            "user_text": self.user_text,
            "assistant_text": self.assistant_text,
            "items": [dict(item) for item in self.items],
        }
        if self.typed_text:
            record["typed_text"] = self.typed_text
        if self.shown_tools:
            record["shown_tools"] = list(self.shown_tools)
            record["full_tools"] = list(self.full_tools)
        return record

    def answers(self, user_text: str) -> bool:
        """Whether this record's turn began with ``user_text`` (recorded or as typed)."""
        return user_text == self.user_text or (
            bool(self.typed_text) and user_text == self.typed_text
        )

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
        typed_text = payload.get("typed_text")
        return cls(
            user_text=user_text,
            assistant_text=assistant_text,
            items=items,
            typed_text=typed_text if isinstance(typed_text, str) else "",
            shown_tools=_names(payload.get("shown_tools")),
            full_tools=_names(payload.get("full_tools")),
        )


def _names(value: Any) -> tuple[str, ...]:
    """A persisted list of tool names; empty when it is not one."""
    if not isinstance(value, list):
        return ()
    return tuple(name for name in value if isinstance(name, str))


def match_turn_evidence(
    messages: Sequence[tuple[str, str]],
    evidence: Sequence[TurnEvidence],
) -> dict[int, TurnEvidence]:
    """Map the index of each user message in ``messages`` to its turn's evidence.

    Walks user/assistant pairs newest first and pairs each with the newest
    unused evidence for the same exchange that is older than the evidence
    matched to the pair after it, so order is preserved and two turns with the
    same texts cannot cross over. Both the reply and the user message must
    match; the user message may match as typed, because the session log keeps
    a bare "yes" while the live transcript recorded its expansion. Pairs
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
        candidates = [
            index
            for index in by_reply.get(assistant_text, ())
            if index < limit and evidence[index].answers(user_text)
        ]
        if candidates:
            limit = candidates[-1]
            matched[position - 1] = evidence[limit]
        position -= 2
    return matched


__all__ = [
    "ASSISTANT_PROVIDER_KEY",
    "ITEM_ASSISTANT",
    "ITEM_TOOL_RESULTS",
    "ITEM_USER",
    "USER_CONTEXT_DIGEST_KEY",
    "USER_SENT_KEY",
    "TurnEvidence",
    "cap_text",
    "match_turn_evidence",
    "provider_record",
    "recorded_provider_message",
]
