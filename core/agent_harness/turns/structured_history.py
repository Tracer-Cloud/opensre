"""Replay earlier turns to the model as typed messages instead of a text block.

Each completed turn is recorded as :class:`~core.state.TurnEvidence` (see
``conversation_recording``): the user's words, every assistant tool-call batch
with its bounded results, and the reply. Building the next request replays the
transcript in order (summary first, then each user/assistant pair, expanded
into its evidence when the pair has one), so tool calls, their arguments and
their results reach the model as the provider's own tool messages.

History only ever grows by whole turns at its end. System prompt, tools and
history therefore stay a stable prefix the provider can cache; the per-turn
context blocks ride in the new user message after it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from config.constants.conversation_history import (
    HISTORY_ASSISTANT_TEXT_MAX_CHARS,
    HISTORY_TOOL_ARGUMENTS_MAX_CHARS,
)
from core.llm.types import ToolCall
from core.messages import (
    AssistantRuntimeMessage,
    RuntimeMessage,
    ToolResultRuntimeMessage,
    UserRuntimeMessage,
)
from core.state import TurnEvidence, match_turn_evidence
from core.state.history_settings import history_tool_result_chars
from core.state.transcript_window import is_summary_message, summary_text
from core.state.turn_evidence import ITEM_ASSISTANT, ITEM_TOOL_RESULTS, ITEM_USER, cap_text
from core.tool.execution import public_tool_input

#: Header of the compacted-history message that opens a long conversation.
SUMMARY_HEADER = (
    "SESSION SUMMARY (earlier turns of this conversation, compacted; the turns after "
    "this message are verbatim):"
)
#: Rough characters per token for budgeting replayed history.
CHARS_PER_TOKEN = 4

# Anthropic accepts tool-use ids matching [A-Za-z0-9_-]{1,64}; other providers
# take any string, so one normalization keeps replay valid everywhere.
_CALL_ID_INVALID_RE = re.compile(r"[^A-Za-z0-9_-]")
_MAX_CALL_ID_CHARS = 64


def replay_call_id(raw_id: Any, fallback: str) -> str:
    """A tool-call id every provider accepts, stable for the same input."""
    cleaned = _CALL_ID_INVALID_RE.sub("_", str(raw_id or ""))[:_MAX_CALL_ID_CHARS]
    return cleaned or fallback


def _content_text(content: Any) -> str:
    """Flatten runtime content (a string or provider blocks) to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            str(block.get("text"))
            for block in content
            if isinstance(block, Mapping) and isinstance(block.get("text"), str)
        ]
        if texts and len(texts) == len(content):
            return "\n".join(texts)
    return json.dumps(content, ensure_ascii=False, default=str)


def _bounded_input(arguments: Any) -> dict[str, Any]:
    """Public tool arguments, replaced by a bounded preview when they are large."""
    public = public_tool_input(arguments) if isinstance(arguments, dict) else {}
    serialized = json.dumps(public, ensure_ascii=False, default=str)
    if len(serialized) <= HISTORY_TOOL_ARGUMENTS_MAX_CHARS:
        bounded: dict[str, Any] = json.loads(serialized)
        return bounded
    return {"truncated_arguments": cap_text(serialized, HISTORY_TOOL_ARGUMENTS_MAX_CHARS)}


def tool_items_from_run(
    messages: Sequence[RuntimeMessage],
    *,
    history_count: int,
) -> tuple[dict[str, Any], ...]:
    """The tool-call batches and their bounded results from one finished run.

    ``messages`` is the run's whole transcript: ``history_count`` replayed
    messages, then this turn's user message, then what the loop added. Host
    nudges and replies the host rejected are left out; the reply the transcript
    records closes the turn (see :func:`build_turn_evidence`), so evidence and
    transcript tell the same story.
    """
    result_limit = history_tool_result_chars()
    items: list[dict[str, Any]] = []
    pending_ids: list[str] = []
    for message in messages[history_count + 1 :]:
        if isinstance(message, AssistantRuntimeMessage) and message.tool_calls:
            call_ids = [
                replay_call_id(call.id, f"call_{len(items)}_{index}")
                for index, call in enumerate(message.tool_calls)
            ]
            calls = [
                {"id": call_id, "name": call.name, "input": _bounded_input(call.input)}
                for call_id, call in zip(call_ids, message.tool_calls, strict=True)
            ]
            items.append(
                {
                    "kind": ITEM_ASSISTANT,
                    "text": cap_text(
                        _content_text(message.content), HISTORY_ASSISTANT_TEXT_MAX_CHARS
                    ),
                    "tool_calls": calls,
                }
            )
            pending_ids = call_ids
        elif isinstance(message, ToolResultRuntimeMessage) and pending_ids:
            results = []
            for index, (call, content) in enumerate(
                zip(message.tool_calls, message.results, strict=False)
            ):
                call_id = (
                    pending_ids[index]
                    if index < len(pending_ids)
                    else replay_call_id(call.id, f"call_{len(items)}_{index}")
                )
                results.append(
                    {
                        "id": call_id,
                        "name": call.name,
                        "content": cap_text(_content_text(content), result_limit),
                    }
                )
            if len(results) == len(pending_ids):
                items.append({"kind": ITEM_TOOL_RESULTS, "results": results})
            else:
                # A batch without a result per call cannot be replayed as tool
                # messages; keep the reply's account of it instead.
                items.pop()
            pending_ids = []
    if pending_ids:
        # The loop stopped before the last batch's results came back.
        items.pop()
    return tuple(items)


def build_turn_evidence(
    user_text: str,
    assistant_text: str,
    tool_items: Sequence[Mapping[str, Any]] = (),
    *,
    typed_text: str = "",
) -> TurnEvidence:
    """One turn as the transcript records it: the user's text, the tool work, the reply.

    ``typed_text`` is the message as typed when ``user_text`` is its expansion.
    """
    items: list[Mapping[str, Any]] = [{"kind": ITEM_USER, "text": user_text}]
    items.extend(dict(item) for item in tool_items)
    items.append(
        {
            "kind": ITEM_ASSISTANT,
            "text": cap_text(assistant_text, HISTORY_ASSISTANT_TEXT_MAX_CHARS),
            "tool_calls": [],
        }
    )
    return TurnEvidence(
        user_text=user_text,
        assistant_text=assistant_text,
        items=tuple(items),
        typed_text=typed_text if typed_text != user_text else "",
    )


def _replay_evidence(evidence: TurnEvidence) -> list[RuntimeMessage]:
    """Typed messages for one recorded turn, keeping every call paired with its results."""
    replayed: list[RuntimeMessage] = []
    items = list(evidence.items)
    index = 0
    while index < len(items):
        item = items[index]
        kind = item.get("kind")
        if kind == ITEM_USER:
            replayed.append(UserRuntimeMessage(content=str(item.get("text") or "")))
            index += 1
            continue
        if kind != ITEM_ASSISTANT:
            index += 1  # results without the call that produced them
            continue
        text = str(item.get("text") or "")
        calls = [call for call in item.get("tool_calls") or () if isinstance(call, Mapping)]
        results_item = items[index + 1] if index + 1 < len(items) else None
        results = (
            [r for r in results_item.get("results") or () if isinstance(r, Mapping)]
            if results_item is not None and results_item.get("kind") == ITEM_TOOL_RESULTS
            else []
        )
        call_ids = [str(call.get("id") or "") for call in calls]
        if calls and [str(r.get("id") or "") for r in results] == call_ids:
            tool_calls = tuple(
                ToolCall(
                    id=call_ids[position],
                    name=str(call.get("name") or "tool"),
                    input=dict(call.get("input") or {}),
                )
                for position, call in enumerate(calls)
            )
            replayed.append(AssistantRuntimeMessage(content=text, tool_calls=tool_calls))
            replayed.append(
                ToolResultRuntimeMessage(
                    tool_calls=tool_calls,
                    results=tuple(str(r.get("content") or "") for r in results),
                )
            )
            index += 2
            continue
        if text:
            replayed.append(AssistantRuntimeMessage(content=text))
        index += 1
    return replayed


def history_messages(
    conversation_messages: Sequence[tuple[str, str]],
    evidence: Sequence[TurnEvidence],
) -> list[RuntimeMessage]:
    """Earlier turns as typed messages, oldest first, ready to precede the new message."""
    matched = match_turn_evidence(conversation_messages, evidence)
    replayed: list[RuntimeMessage] = []
    index = 0
    while index < len(conversation_messages):
        role, text = conversation_messages[index]
        if is_summary_message((role, text)):
            replayed.append(
                UserRuntimeMessage(content=f"{SUMMARY_HEADER}\n{summary_text((role, text))}")
            )
            index += 1
            continue
        record = matched.get(index)
        if record is not None:
            replayed.extend(_replay_evidence(record))
            index += 2
            continue
        if text:
            if role == "user":
                replayed.append(UserRuntimeMessage(content=text))
            elif role == "assistant":
                replayed.append(AssistantRuntimeMessage(content=text))
        index += 1
    return replayed


def history_chars(
    conversation_messages: Sequence[tuple[str, str]],
    evidence: Sequence[TurnEvidence],
) -> int:
    """Characters the replayed history adds to a request."""
    matched = match_turn_evidence(conversation_messages, evidence)
    total = 0
    index = 0
    while index < len(conversation_messages):
        record = matched.get(index)
        if record is not None:
            total += record.char_count()
            index += 2
            continue
        total += len(conversation_messages[index][1])
        index += 1
    return total


def estimate_history_tokens(
    conversation_messages: Sequence[tuple[str, str]],
    evidence: Sequence[TurnEvidence],
) -> int:
    """Estimated tokens the replayed history adds to a request."""
    return history_chars(conversation_messages, evidence) // CHARS_PER_TOKEN


__all__ = [
    "CHARS_PER_TOKEN",
    "SUMMARY_HEADER",
    "build_turn_evidence",
    "estimate_history_tokens",
    "history_chars",
    "history_messages",
    "replay_call_id",
    "tool_items_from_run",
]
