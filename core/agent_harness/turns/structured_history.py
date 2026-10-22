"""Replay earlier turns to the model as typed messages instead of a text block.

Each completed turn is recorded as :class:`~core.state.TurnEvidence` (see
``conversation_recording``): the user's words, every assistant tool-call batch
with its bounded results, and the reply. Building the next request replays the
transcript in order (summary first, then each user/assistant pair, expanded
into its evidence when the pair has one), so tool calls, their arguments and
their results reach the model as the provider's own tool messages.

History only ever grows by whole turns at its end, and each turn replays as
it was sent: the user message with the context it carried, the tool calls and
results in the shape the model received them, and each tool-call batch as the
provider's own message, reasoning included, when the client that wrote it is
the one replaying it. System prompt, tools and history therefore stay a stable
prefix the provider can cache; the per-turn context blocks ride in the new
user message after it, with the session-context section only when it changed
(:func:`session_context_to_send`).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

from config.constants.conversation_history import (
    HISTORY_ASSISTANT_TEXT_MAX_CHARS,
    HISTORY_PROVIDER_MESSAGE_MAX_AGE_SECONDS,
    HISTORY_TOOL_ARGUMENTS_MAX_CHARS,
)
from config.constants.tool_discovery import REQUEST_SCOPED_RESULTS_METADATA_KEY
from core.llm.types import ToolCall
from core.messages import (
    AssistantRuntimeMessage,
    RuntimeMessage,
    ToolResultRuntimeMessage,
    UserRuntimeMessage,
)
from core.state import ShownTools, TurnEvidence, match_turn_evidence
from core.state.history_settings import history_tool_result_chars, structured_history_enabled
from core.state.transcript_window import is_summary_message, summary_text
from core.state.turn_evidence import (
    ASSISTANT_PROVIDER_KEY,
    ITEM_ASSISTANT,
    ITEM_TOOL_RESULTS,
    ITEM_USER,
    USER_CONTEXT_DIGEST_KEY,
    USER_SENT_KEY,
    cap_text,
    provider_record,
    recorded_provider_message,
)
from core.tool import history_replay_byte_budget, truncate_output_text
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


def _replayed_result(content: Any, *, byte_budget: int, char_limit: int | None) -> Any:
    """A tool observation in the shape replay sends it back.

    A list of content blocks is kept as the model received it, so the replayed
    result serializes to the same bytes. Text was bounded by the shared output
    policy when the tool returned it, so the replay budget leaves it unchanged
    unless an explicit legacy character cap is set.
    """
    if isinstance(content, list):
        return content
    text = truncate_output_text(_content_text(content), byte_budget)
    return cap_text(text, char_limit) if char_limit is not None else text


def _bounded_input(arguments: Any) -> dict[str, Any]:
    """Public tool arguments, replaced by a bounded preview when they are large."""
    public = public_tool_input(arguments) if isinstance(arguments, dict) else {}
    serialized = json.dumps(public, ensure_ascii=False, default=str)
    if len(serialized) <= HISTORY_TOOL_ARGUMENTS_MAX_CHARS:
        bounded: dict[str, Any] = json.loads(serialized)
        return bounded
    return {"truncated_arguments": cap_text(serialized, HISTORY_TOOL_ARGUMENTS_MAX_CHARS)}


def _provider_record(
    message: AssistantRuntimeMessage, text: str, calls: Sequence[Mapping[str, Any]]
) -> dict[str, Any] | None:
    """The provider's own message for a tool-call batch, when resending it says what the item says.

    Only while the item kept the message whole: its text within the cap, each
    call under its own id with all of its arguments public. Otherwise the
    provider message would resend what the item cut, renamed or redacted.
    """
    if message.provider_payload is None or not message.provider_binding:
        return None
    if len(text) > HISTORY_ASSISTANT_TEXT_MAX_CHARS:
        return None
    for call, recorded in zip(message.tool_calls, calls, strict=True):
        if recorded["id"] != call.id or recorded["input"] != call.input:
            return None
    return provider_record(
        message.provider_binding, message.provider_payload, recorded_at=time.time()
    )


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
    transcript tell the same story. Results the loop marked request-scoped (a
    loaded skill body) are left out too; a tool-search result stays, because
    the tools it revealed stay visible for the session.
    """
    result_limit = history_tool_result_chars()
    result_byte_budget = history_replay_byte_budget()
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
            text = _content_text(message.content)
            item: dict[str, Any] = {
                "kind": ITEM_ASSISTANT,
                "text": cap_text(text, HISTORY_ASSISTANT_TEXT_MAX_CHARS),
                "tool_calls": calls,
            }
            record = _provider_record(message, text, calls)
            if record is not None:
                item[ASSISTANT_PROVIDER_KEY] = record
            items.append(item)
            pending_ids = call_ids
        elif isinstance(message, ToolResultRuntimeMessage) and pending_ids:
            hidden_raw = message.metadata.get(REQUEST_SCOPED_RESULTS_METADATA_KEY, ())
            hidden = {
                index
                for index in hidden_raw
                if isinstance(index, int) and 0 <= index < len(pending_ids)
            }
            kept_indices = [index for index in range(len(pending_ids)) if index not in hidden]
            if not kept_indices:
                # A loaded skill body belongs to the request that loaded it;
                # the next request rebuilds its own skill context.
                items.pop()
                pending_ids = []
                continue
            original_ids = pending_ids
            if hidden and items and items[-1].get("kind") == ITEM_ASSISTANT:
                calls = list(items[-1].get("tool_calls") or ())
                items[-1]["tool_calls"] = [calls[index] for index in kept_indices]
                # The provider's message still asks for the calls left out.
                items[-1].pop(ASSISTANT_PROVIDER_KEY, None)
            pending_ids = [original_ids[index] for index in kept_indices]
            results = []
            pairs = list(zip(message.tool_calls, message.results, strict=False))
            for result_index, original_index in enumerate(kept_indices):
                if original_index >= len(pairs):
                    continue
                call, content = pairs[original_index]
                call_id = pending_ids[result_index]
                results.append(
                    {
                        "id": call_id,
                        "name": call.name,
                        "content": _replayed_result(
                            content, byte_budget=result_byte_budget, char_limit=result_limit
                        ),
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
    sent_text: str = "",
    context_digest: str = "",
    shown_tools: ShownTools | None = None,
) -> TurnEvidence:
    """One turn as the transcript records it: the user's text, the tool work, the reply.

    ``typed_text`` is the message as typed when ``user_text`` is its expansion.
    ``sent_text`` is the message exactly as the model received it, replayed in
    its place so later requests keep the prefix the provider cached;
    ``context_digest`` fingerprints the session context that message carried;
    ``shown_tools`` is the conversation's tool list as the turn ended.
    """
    user_item: dict[str, Any] = {"kind": ITEM_USER, "text": user_text}
    if sent_text and sent_text != user_text:
        user_item[USER_SENT_KEY] = sent_text
    if context_digest:
        user_item[USER_CONTEXT_DIGEST_KEY] = context_digest
    items: list[Mapping[str, Any]] = [user_item]
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
        shown_tools=tuple(shown_tools.names) if shown_tools is not None else (),
        full_tools=tuple(sorted(shown_tools.full)) if shown_tools is not None else (),
    )


def _stored_result(content: Any) -> Any:
    """A recorded observation as replay sends it: block lists as recorded, anything else as text."""
    if isinstance(content, list):
        return content
    return str(content or "")


def _resendable_provider_message(
    item: Mapping[str, Any], *, now: float
) -> tuple[dict[str, Any] | None, str | None]:
    """The provider message an assistant item recorded and its binding, unless it is too old."""
    recorded = recorded_provider_message(item)
    if recorded is None:
        return None, None
    message, binding, recorded_at = recorded
    if now - recorded_at > HISTORY_PROVIDER_MESSAGE_MAX_AGE_SECONDS:
        return None, None
    return message, binding


def _replay_evidence(evidence: TurnEvidence, *, now: float) -> list[RuntimeMessage]:
    """Typed messages for one recorded turn, keeping every call paired with its results.

    A tool-call batch recorded with the provider's own message carries it, so the
    client that sent it resends it as sent, reasoning included (see
    :class:`~core.messages.MessageMapper`).
    """
    replayed: list[RuntimeMessage] = []
    items = list(evidence.items)
    index = 0
    while index < len(items):
        item = items[index]
        kind = item.get("kind")
        if kind == ITEM_USER:
            sent = item.get(USER_SENT_KEY) or item.get("text") or ""
            replayed.append(UserRuntimeMessage(content=str(sent)))
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
            provider_payload, provider_binding = _resendable_provider_message(item, now=now)
            replayed.append(
                AssistantRuntimeMessage(
                    content=text,
                    tool_calls=tool_calls,
                    provider_payload=provider_payload,
                    provider_binding=provider_binding,
                )
            )
            replayed.append(
                ToolResultRuntimeMessage(
                    tool_calls=tool_calls,
                    results=tuple(_stored_result(r.get("content")) for r in results),
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
    now = time.time()
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
            replayed.extend(_replay_evidence(record, now=now))
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


def context_digest(context: str) -> str:
    """A short fingerprint of a session-context section, recorded with the turn that carried it."""
    return hashlib.sha256(context.encode("utf-8")).hexdigest()[:16]


def replayed_context_digest(
    conversation_messages: Sequence[tuple[str, str]],
    evidence: Sequence[TurnEvidence],
) -> str:
    """Fingerprint of the newest session context the replayed history carries; ``""`` for none.

    Only turns replayed from their evidence count: a turn that replays as plain
    text (or was folded into a compaction summary) no longer carries the
    message it was sent with.
    """
    matched = match_turn_evidence(conversation_messages, evidence)
    for position in sorted(matched, reverse=True):
        for item in matched[position].items:
            digest = item.get(USER_CONTEXT_DIGEST_KEY) if item.get("kind") == ITEM_USER else None
            if digest:
                return str(digest)
    return ""


def session_context_to_send(
    context: str,
    conversation_messages: Sequence[tuple[str, str]],
    evidence: Sequence[TurnEvidence],
) -> str:
    """The session context the new user message must carry: all of it, or ``""``.

    ``""`` when the replayed history already carries this exact section, so an
    unchanged section is sent once and then read from the provider's cache.
    Without structured history nothing is replayed, so every message carries it.
    """
    if not context:
        return ""
    if not structured_history_enabled():
        return context
    if replayed_context_digest(conversation_messages, evidence) == context_digest(context):
        return ""
    return context


__all__ = [
    "CHARS_PER_TOKEN",
    "SUMMARY_HEADER",
    "build_turn_evidence",
    "context_digest",
    "estimate_history_tokens",
    "history_chars",
    "history_messages",
    "replay_call_id",
    "replayed_context_digest",
    "session_context_to_send",
    "tool_items_from_run",
]
