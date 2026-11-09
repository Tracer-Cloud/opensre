"""Load the tools a search reveals by reference, so a reveal leaves the cached prefix intact.

Anthropic caches the request prefix tools → system → messages, so a tool added
to ``tools`` mid-conversation re-prices everything after it. A tool declared with
``defer_loading: true`` is not part of that prefix: the model sees it only where
a ``tool_result`` carries a ``tool_reference`` to it, and the API expands the
reference into the tool's definition at that point of the conversation (the
API's custom tool search).

A run declares every catalog tool the conversation was not shown up front as
deferred from its first request on, so ``tools`` reads the same before and
after a search. Each request then references, in the result of the
``tool_search`` call that first found it, every deferred tool the conversation
revealed: the reveal reaches the model as new conversation, and a later turn's
replay of that result carries the same reference.

Best-effort, like the cache markers: only models that load tools by reference
defer (:func:`supports_deferred_tools`), and a 400 that blames the feature
(:func:`is_deferred_tools_unsupported_error`) is retried with every revealed tool
loaded up front, as before, after which the client stops deferring.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Final

from config.constants.llm import ANTHROPIC_DEFER_LOADING_KEY
from config.constants.tool_discovery import TOOL_SEARCH_MATCHES_KEY, TOOL_SEARCH_NAME
from core.llm.transports.sdk.anthropic_cache import BEDROCK_UNNAMED_REJECTION_SIGNALS

# Tool search (and with it ``tool_reference``) needs an Anthropic model from
# version 4.5 on; the Fable and Mythos families have it in every version.
_MODEL_VERSION_RE: Final = re.compile(
    r"claude-(opus|sonnet|haiku|fable|mythos)-(\d+)(?:[-.](\d{1,2})(?!\d))?"
)
_FIRST_VERSION_WITH_TOOL_REFERENCES: Final = (4, 5)
_ALWAYS_SUPPORTED_FAMILIES: Final = frozenset({"fable", "mythos"})

#: Substrings a provider 400 uses when the request failed *because of* deferred
#: loading: the deferred field, the reference blocks, or the beta that enables them.
_DEFERRED_TOOLS_UNSUPPORTED_SIGNALS: Final[tuple[str, ...]] = (
    ANTHROPIC_DEFER_LOADING_KEY,
    "tool_reference",
    "tool reference",
    "tool-search",
    "tool search",
    "anthropic_beta",
    "anthropic-beta",
    *BEDROCK_UNNAMED_REJECTION_SIGNALS,
)


def supports_deferred_tools(model: str | None) -> bool:
    """Whether ``model`` loads tools by reference (version 4.5 on, Fable, Mythos).

    Matches the family and version anywhere in the id, so Bedrock ids
    (``us.anthropic.claude-opus-4-7-v1:0``) count; an id that names neither
    (an application inference profile, a gateway alias) does not.
    """
    match = _MODEL_VERSION_RE.search((model or "").lower())
    if match is None:
        return False
    family, major, minor = match.group(1), int(match.group(2)), int(match.group(3) or 0)
    return family in _ALWAYS_SUPPORTED_FAMILIES or (major, minor) >= (
        _FIRST_VERSION_WITH_TOOL_REFERENCES
    )


def deferred_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """``schema`` declared for loading by reference only."""
    return {**schema, ANTHROPIC_DEFER_LOADING_KEY: True}


def is_deferred(schema: Any) -> bool:
    """Whether a tool schema is declared for loading by reference only."""
    return isinstance(schema, dict) and schema.get(ANTHROPIC_DEFER_LOADING_KEY) is True


def deferred_tool_request(
    messages: list[dict[str, Any]],
    tools: Sequence[dict[str, Any]],
    *,
    enabled: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """``(messages, tools, deferring)`` for one request.

    ``tools`` holds the schemas of the tools the conversation was shown, in
    order, then the deferred declarations. When ``enabled``, each tool search
    result references the deferred tools it revealed, those leave the up-front
    list, and every declaration follows the tools still loaded up front. A
    revealed tool no result references (its result was compacted away or could
    not be read) stays loaded up front. Otherwise — or when nothing would stay
    up front, which the API rejects — the declarations are dropped and the
    request is shaped as it was before deferred loading. ``deferring`` says
    whether the request declares any deferred tool.
    """
    up_front = [tool for tool in tools if not is_deferred(tool)]
    declared = [tool for tool in tools if is_deferred(tool)]
    if not enabled or not declared:
        return messages, up_front, False
    referencing, referenced = _reference_revealed_tools(
        messages, {str(tool.get("name")) for tool in declared}
    )
    loaded = [tool for tool in up_front if tool.get("name") not in referenced]
    if not loaded:
        return messages, up_front, False
    loaded_names = {tool.get("name") for tool in loaded}
    deferred = [tool for tool in declared if tool.get("name") not in loaded_names]
    return referencing, [*loaded, *deferred], bool(deferred)


def is_deferred_tools_unsupported_error(err: Exception) -> bool:
    """True when a bad-request error blames deferred loading or tool references.

    Only meaningful for 400-class errors — callers must check the error type
    first so unrelated failures (auth, rate limits) are never eaten.
    """
    message = str(err).lower()
    return any(signal in message for signal in _DEFERRED_TOOLS_UNSUPPORTED_SIGNALS)


def _reference_revealed_tools(
    messages: list[dict[str, Any]], deferred_names: set[str]
) -> tuple[list[dict[str, Any]], set[str]]:
    """``messages`` with each deferred tool referenced by the first search result that found it.

    Copies what it changes and never mutates ``messages``: the loop reuses the
    transcript across iterations and retries.
    """
    call_names: dict[Any, Any] = {}
    referenced: set[str] = set()
    out: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            out.append(message)
            continue
        if message.get("role") == "assistant":
            call_names.update(
                (_field(block, "id"), _field(block, "name"))
                for block in content
                if _field(block, "type") == "tool_use"
            )
            out.append(message)
            continue
        blocks = [
            _with_references(block, call_names, deferred_names, referenced) for block in content
        ]
        changed = any(new is not old for new, old in zip(blocks, content, strict=True))
        out.append({**message, "content": blocks} if changed else message)
    return out, referenced


def _with_references(
    block: Any, call_names: dict[Any, Any], deferred_names: set[str], referenced: set[str]
) -> Any:
    """A tool search result that also references the deferred tools it found first.

    Adds those names to ``referenced``; any other block comes back unchanged.
    """
    if not (
        isinstance(block, dict)
        and block.get("type") == "tool_result"
        and call_names.get(block.get("tool_use_id")) == TOOL_SEARCH_NAME
    ):
        return block
    found = [
        name
        for name in dict.fromkeys(_search_matches(block.get("content")))
        if name in deferred_names and name not in referenced
    ]
    if not found:
        return block
    referenced.update(found)
    return {
        **block,
        "content": [
            {"type": "text", "text": block["content"]},
            *({"type": "tool_reference", "tool_name": name} for name in found),
        ],
    }


def _search_matches(content: Any) -> list[str]:
    """Names a tool search result reports as found; ``[]`` when it cannot be read.

    The client sends a tool's output as a JSON-encoded string, so the payload
    may arrive encoded twice. Output cut to the tool-output budget no longer
    parses, and its tools are loaded up front instead.
    """
    if not isinstance(content, str):
        return []
    try:
        payload: Any = json.loads(content)
        if isinstance(payload, str):
            payload = json.loads(payload)
    except ValueError:
        return []
    matches = payload.get(TOOL_SEARCH_MATCHES_KEY) if isinstance(payload, dict) else None
    if not isinstance(matches, list):
        return []
    return [
        match["name"]
        for match in matches
        if isinstance(match, dict) and isinstance(match.get("name"), str)
    ]


def _field(block: Any, name: str) -> Any:
    return block.get(name) if isinstance(block, dict) else getattr(block, name, None)


__all__ = [
    "deferred_schema",
    "deferred_tool_request",
    "is_deferred",
    "is_deferred_tools_unsupported_error",
    "supports_deferred_tools",
]
