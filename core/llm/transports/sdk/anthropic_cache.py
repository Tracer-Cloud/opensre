"""Anthropic prompt-cache breakpoint markers and replay-stable content blocks.

A leaf module: both the agent client and the plain messages client mark cache
breakpoints, and neither imports the other. Anthropic caches the request prefix
up to each ``cache_control`` marker, so a byte-stable prefix plus a marker turns
resent input into cache reads (~0.1x input price) instead of full-price tokens.
The block builders give a live assistant turn and its later replay one shape,
so the history a request resends matches the prefix the provider cached.

Marking a prefix shorter than the model's minimum cacheable length is safe: the
request succeeds and simply is not cached, so callers do not need to measure
prompt size before marking.

Not every deployment supports the markers themselves: Bedrock rejects
``cache_control`` with a 400 for Claude models that predate prompt caching.
Clients therefore treat caching as best-effort — on a 400 that blames the
markers (:func:`is_cache_unsupported_error`), they retry once without markers
(:func:`strip_cache_markers`) and stop marking for the client's lifetime, so an
unsupported model works uncached instead of failing every call.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from config.constants.llm import ANTHROPIC_DEFER_LOADING_KEY

#: Anthropic's 5-minute ephemeral cache. Written once, read by later requests
#: whose prefix matches byte-for-byte.
ANTHROPIC_CACHE_CONTROL: Final[dict[str, str]] = {"type": "ephemeral"}


def text_block(text: str) -> dict[str, Any]:
    """A text content block, in the one shape a live turn and its replay both send."""
    return {"type": "text", "text": text}


def tool_use_block(call_id: str, name: str, tool_input: Any) -> dict[str, Any]:
    """A tool-use content block, in the one shape a live turn and its replay both send."""
    return {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}


def _field(block: Any, name: str) -> Any:
    return block.get(name) if isinstance(block, dict) else getattr(block, name, None)


def request_block(block: Any) -> Any:
    """A response content block as the SDK encodes it in a request body, as a plain dict."""
    if isinstance(block, dict):
        return block
    dump = getattr(block, "model_dump", None)
    if not callable(dump):
        return block
    return dump(
        mode="json",
        exclude_unset=True,
        by_alias=True,
        exclude=getattr(block, "__api_exclude__", None),
    )


def replay_stable_blocks(blocks: Sequence[Any]) -> list[Any]:
    """A response's content with its text and tool-use blocks in replay shape.

    The SDK echoes a response block with every field the API set, in its own
    field order, while a later turn rebuilds the same text and calls from the
    recorded turn (:func:`text_block`, :func:`tool_use_block`). Sending both in
    one shape keeps the cached prefix byte-identical from turn to turn. A block
    a replay cannot rebuild (thinking, citations, server or programmatic tool
    use) is sent unchanged, as the plain dict the SDK would encode it to, so the
    message can be recorded and replayed later byte for byte.
    """
    stable: list[Any] = []
    for block in blocks:
        kind = _field(block, "type")
        caller = _field(block, "caller")
        if kind == "text" and not _field(block, "citations"):
            stable.append(text_block(str(_field(block, "text") or "")))
        elif (
            kind == "tool_use"
            and not _field(block, "toolset_name")
            and (caller is None or _field(caller, "type") == "direct")
        ):
            stable.append(
                tool_use_block(
                    str(_field(block, "id")), str(_field(block, "name")), _field(block, "input")
                )
            )
        else:
            stable.append(request_block(block))
    return stable


def cached_system(system: str) -> list[dict[str, Any]]:
    """Return ``system`` as a single text block carrying a cache breakpoint."""
    return [
        {
            "type": "text",
            "text": system,
            "cache_control": dict(ANTHROPIC_CACHE_CONTROL),
        }
    ]


def tools_with_cache(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark the last tool loaded up front, caching the whole tool block before it."""
    if not tools:
        return tools
    cached = [dict(tool) for tool in tools]
    last = next(
        (
            index
            for index in range(len(cached) - 1, -1, -1)
            if cached[index].get(ANTHROPIC_DEFER_LOADING_KEY) is not True
        ),
        None,
    )
    if last is not None:
        cached[last] = {**cached[last], "cache_control": dict(ANTHROPIC_CACHE_CONTROL)}
    return cached


def messages_with_cache(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark the newest message's last content block as a cache breakpoint.

    Tools and system cover the static prefix; this marker lets the growing
    conversation history accrue incremental cache hits across ReAct
    iterations. Copies, never mutates — the caller reuses ``messages`` on
    retries. Content that cannot carry a marker (empty text, unknown shapes)
    is left untouched.
    """
    if not messages:
        return messages
    last = messages[-1]
    content = last.get("content")
    if isinstance(content, str) and content:
        marked_content: list[Any] = [
            {
                "type": "text",
                "text": content,
                "cache_control": dict(ANTHROPIC_CACHE_CONTROL),
            }
        ]
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        # Copy every block, not just the marked one: the transcript is reused
        # across ReAct iterations, and a shared dict would let a later payload
        # mutation write through into live history.
        marked_content = [
            dict(block) if isinstance(block, dict) else block for block in content[:-1]
        ]
        marked_content.append({**content[-1], "cache_control": dict(ANTHROPIC_CACHE_CONTROL)})
    else:
        return messages
    return [*messages[:-1], {**last, "content": marked_content}]


#: Bedrock's ValidationException often rejects a request without naming the
#: offending field, so a fallback keyed on a feature has to count this generic
#: wording as a signal too. Matched only on a 400, where the request is already
#: failing.
BEDROCK_UNNAMED_REJECTION_SIGNALS: Final[tuple[str, ...]] = (
    "does not support the requested feature",
    "invalid request body",
)

#: Substrings a provider 400 uses when the request failed *because of* the
#: cache markers: Anthropic names the offending ``cache_control`` field,
#: Bedrock's Converse-era wording says "cachePoint" / "prompt caching".
_CACHE_UNSUPPORTED_SIGNALS: Final[tuple[str, ...]] = (
    "cache_control",
    "cachepoint",
    "prompt caching",
    "prompt cache",
    *BEDROCK_UNNAMED_REJECTION_SIGNALS,
)


def is_cache_unsupported_error(err: Exception) -> bool:
    """True when a bad-request error blames the cache markers themselves.

    Only meaningful for 400-class errors — callers must check the error type
    first so unrelated failures (auth, rate limits) are never eaten.
    """
    message = str(err).lower()
    return any(signal in message for signal in _CACHE_UNSUPPORTED_SIGNALS)


def strip_cache_markers(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Copy of request ``kwargs`` with every marker this module adds removed.

    Strips exactly the spots :func:`cached_system`, :func:`tools_with_cache`,
    and :func:`messages_with_cache` write to — system blocks, top-level tool
    schemas, and message content blocks. It deliberately does not recurse into
    tool ``input_schema`` bodies, where ``cache_control`` could be a
    legitimate user-defined property name.
    """
    out = dict(kwargs)
    system = out.get("system")
    if isinstance(system, list):
        blocks = [_block_without_marker(block) for block in system]
        # Exactly invert cached_system: a lone text block goes back to the plain
        # string a non-caching client would have sent, since a model that rejects
        # the markers may not accept block-form system either.
        if (
            len(blocks) == 1
            and isinstance(blocks[0], dict)
            and set(blocks[0]) == {"type", "text"}
            and blocks[0].get("type") == "text"
        ):
            out["system"] = blocks[0]["text"]
        else:
            out["system"] = blocks
    tools = out.get("tools")
    if isinstance(tools, list):
        out["tools"] = [_block_without_marker(tool) for tool in tools]
    messages = out.get("messages")
    if isinstance(messages, list):
        out["messages"] = [_message_without_marker(message) for message in messages]
    return out


def _block_without_marker(block: Any) -> Any:
    if isinstance(block, dict) and "cache_control" in block:
        return {key: value for key, value in block.items() if key != "cache_control"}
    return block


def _message_without_marker(message: Any) -> Any:
    if not isinstance(message, dict):
        return message
    content = message.get("content")
    if not isinstance(content, list):
        return message
    return {**message, "content": [_block_without_marker(block) for block in content]}


__all__ = [
    "ANTHROPIC_CACHE_CONTROL",
    "BEDROCK_UNNAMED_REJECTION_SIGNALS",
    "cached_system",
    "is_cache_unsupported_error",
    "messages_with_cache",
    "replay_stable_blocks",
    "request_block",
    "strip_cache_markers",
    "text_block",
    "tool_use_block",
    "tools_with_cache",
]
