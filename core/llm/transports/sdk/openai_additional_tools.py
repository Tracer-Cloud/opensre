"""Load the tools a search reveals where the search found them, so a reveal keeps the cached prefix.

OpenAI matches a request's cached prefix with the tool definitions ahead of the
conversation, so a tool added to ``tools`` mid-conversation re-prices the whole
request: measured on the hosted route, the request after a reveal read nothing
from the cache. The Responses API also takes tools as an ``additional_tools``
input item. Its tools become callable from that point of the conversation on,
and everything before the item reads as it did.

Each request therefore keeps in ``tools`` what the conversation was shown up
front, and loads every tool a ``tool_search`` result reports as newly visible
from an item placed after that result. A later turn's replay of the result
places the same item at the same point, so the reveal stays new conversation.
A revealed tool no result reports (its result was compacted away, cut, or
replaced by the context-budget stub) stays in ``tools``.

Best-effort, like deferred loading on Anthropic: a 400 that names the item
(:func:`is_additional_tools_unsupported_error`) is retried with every tool in
``tools``, as before, after which the client stops loading tools this way.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from config.constants.tool_discovery import TOOL_SEARCH_NAME
from core.llm.shared.tool_search_result import tool_search_matches

_ADDITIONAL_TOOLS_ITEM: Final = "additional_tools"
_FUNCTION_CALL_ITEM: Final = "function_call"
_FUNCTION_CALL_OUTPUT_ITEM: Final = "function_call_output"
#: Key of a tool search match that says the search made the tool visible, as
#: opposed to expanding a tool the conversation already had.
_NEWLY_VISIBLE_KEY: Final = "newly_visible"

#: Substrings a provider 400 uses when the request failed *because of* the item.
_UNSUPPORTED_SIGNALS: Final[tuple[str, ...]] = ("additional_tools", "additional tools")


def additional_tools_request(
    input_items: list[dict[str, Any]],
    tools: Sequence[dict[str, Any]],
    *,
    enabled: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """``(input, tools, loading)`` for one Responses request.

    ``tools`` holds the Responses specs of every tool the conversation shows,
    in order. When ``enabled``, each tool a ``tool_search`` result first
    reports as newly visible leaves that list and is loaded by an
    ``additional_tools`` item placed after the results of the response that
    searched. ``loading`` says whether the request carries such an item; a
    request that would be left with no declared tool keeps its former shape.
    Never mutates ``input_items``: the loop reuses the transcript across
    iterations and retries.
    """
    up_front = list(tools)
    if not enabled:
        return input_items, up_front, False
    by_name = {str(tool.get("name")): tool for tool in up_front if tool.get("type") == "function"}
    search_calls: set[str] = set()
    loaded: set[str] = set()
    found: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for item in input_items:
        kind = item.get("type")
        if found and kind != _FUNCTION_CALL_OUTPUT_ITEM:
            # Results of one response stay together; its reveals follow them.
            items.append(_additional_tools_item(found))
            found = []
        items.append(item)
        if kind == _FUNCTION_CALL_ITEM and item.get("name") == TOOL_SEARCH_NAME:
            search_calls.add(str(item.get("call_id")))
        elif kind == _FUNCTION_CALL_OUTPUT_ITEM and str(item.get("call_id")) in search_calls:
            for name in _newly_visible(item.get("output")):
                if name in by_name and name not in loaded:
                    loaded.add(name)
                    found.append(by_name[name])
    if found:
        items.append(_additional_tools_item(found))
    remaining = [tool for tool in up_front if tool.get("name") not in loaded]
    if not loaded or not remaining:
        # Nothing to load, or nothing would stay declared: shaped as before.
        return input_items, up_front, False
    return items, remaining, True


def is_additional_tools_unsupported_error(err: Exception) -> bool:
    """True when a bad-request error blames the ``additional_tools`` item.

    Only meaningful for 400-class errors — callers must check the error type
    first so unrelated failures (auth, rate limits) are never eaten.
    """
    message = str(err).lower()
    return any(signal in message for signal in _UNSUPPORTED_SIGNALS)


def _additional_tools_item(tools: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": _ADDITIONAL_TOOLS_ITEM, "role": "developer", "tools": tools}


def _newly_visible(output: Any) -> list[str]:
    """Names a tool search result made visible; the search tool itself is never one."""
    return [
        match["name"]
        for match in tool_search_matches(output)
        if match.get(_NEWLY_VISIBLE_KEY) is True and match["name"] != TOOL_SEARCH_NAME
    ]


__all__ = ["additional_tools_request", "is_additional_tools_unsupported_error"]
