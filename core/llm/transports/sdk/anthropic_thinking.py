"""Keep resent Anthropic thinking blocks valid under the API's history-editing check.

A thinking block's signature binds it to the conversation that produced it: the
system prompt, the tools, every earlier message (thinking aside) and the thinking
block before it. On a later request the API rejects a block whose conversation
changed since — with a 400 for the accounts it enforces the check for — even when
the change is legitimate: a compaction summary, a tool the conversation was shown
since, a turn replayed with less than it sent.

The client therefore records that binding on each assistant message it receives
(``ANTHROPIC_THINKING_BINDING_KEY``, from :func:`bind_thinking`) and, before every
request, drops the thinking blocks of the messages whose binding no longer holds.
An append-only conversation keeps every block, so its cached prefix keeps
matching; an edit costs the reasoning after it, never the request. Like the API,
the check lets a leading run of blocks go.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any, Final

from config.constants.llm import ANTHROPIC_THINKING_BINDING_KEY
from core.llm.transports.sdk.anthropic_cache import request_block

_THINKING_BLOCK_TYPES: Final = frozenset({"thinking", "redacted_thinking"})
_INTERNAL_KEY_PREFIX: Final = "_opensre_"


def is_thinking_block(block: Any) -> bool:
    """Whether ``block`` is a ``thinking`` or ``redacted_thinking`` content block."""
    kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
    return kind in _THINKING_BLOCK_TYPES


def _thinking_blocks(message: dict[str, Any]) -> list[Any]:
    content = message.get("content")
    if message.get("role") != "assistant" or not isinstance(content, list):
        return []
    return [block for block in content if is_thinking_block(block)]


def carries_thinking(messages: Sequence[dict[str, Any]]) -> bool:
    """Whether any assistant message in ``messages`` carries a thinking block."""
    return any(_thinking_blocks(message) for message in messages)


def _json_default(value: Any) -> Any:
    encoded = request_block(value)
    return str(value) if encoded is value else encoded


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), default=_json_default
    ).encode("utf-8")


def _block_id(block: Any) -> str:
    return hashlib.sha256(_canonical(block)).hexdigest()[:32]


def _bound_part(message: dict[str, Any]) -> dict[str, Any]:
    """``message`` as the binding sees it: no internal markers, no thinking blocks."""
    visible = {
        key: value
        for key, value in message.items()
        if not str(key).startswith(_INTERNAL_KEY_PREFIX)
    }
    content = visible.get("content")
    if isinstance(content, list):
        visible["content"] = [block for block in content if not is_thinking_block(block)]
    return visible


def _holds(recorded: Any, prefix: str, previous: str) -> bool:
    """Whether a recorded binding matches the request built so far."""
    if not isinstance(recorded, dict) or recorded.get("prefix") != prefix:
        return False
    # No kept block before this one: the blocks before it were a leading run.
    return not previous or recorded.get("after") == previous


def bind_thinking(
    messages: Sequence[dict[str, Any]],
    *,
    system: str | None,
    tools: Sequence[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """The messages to send, without thinking the API would reject, and a reply's binding.

    An assistant message recorded with a binding keeps its thinking blocks only
    while the binding holds here: the same system prompt, tools and earlier
    messages (thinking aside), and the same thinking block last before it, or
    none at all. A message without a recorded binding keeps its blocks. A message
    left with no content once its thinking is dropped is left out. The returned
    binding is the one a response to the returned messages is bound to.
    """
    prefix = hashlib.sha256(_canonical([system or "", list(tools or ())]))
    previous = ""
    sent: list[dict[str, Any]] = []
    for message in messages:
        blocks = _thinking_blocks(message)
        if blocks:
            recorded = message.get(ANTHROPIC_THINKING_BINDING_KEY)
            if recorded is None or _holds(recorded, prefix.hexdigest(), previous):
                previous = _block_id(blocks[-1])
            else:
                content = [block for block in message["content"] if not is_thinking_block(block)]
                if not content:
                    continue
                message = {**message, "content": content}
        sent.append(message)
        prefix.update(_canonical(_bound_part(message)))
    return sent, {"prefix": prefix.hexdigest(), "after": previous}


__all__ = [
    "bind_thinking",
    "carries_thinking",
    "is_thinking_block",
]
