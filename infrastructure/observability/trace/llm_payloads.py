"""Shape LLM request / response payloads for observation sinks.

Backends render a list of ``{"role", "content"}`` messages as a chat
transcript and an assistant message's ``tool_calls`` (OpenAI function shape,
JSON-encoded ``arguments``) as tool-call cards, so every provider's request is
normalised to that shape here rather than at each call site.

Callers build these payloads only when
:func:`~infrastructure.observability.trace.observations.is_observation_sink_active`
is true; nothing here runs on an uninstrumented process.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def _tool_call_card(tool_call: Any) -> dict[str, Any]:
    arguments = getattr(tool_call, "input", None)
    try:
        encoded = json.dumps(arguments, default=str)
    except (TypeError, ValueError):
        encoded = json.dumps(str(arguments))
    return {
        "id": str(getattr(tool_call, "id", "") or ""),
        "type": "function",
        "function": {
            "name": str(getattr(tool_call, "name", "") or ""),
            "arguments": encoded,
        },
    }


def generation_input(
    system: str | None,
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """The full prompt as a chat transcript: system first, then the provider messages."""
    transcript: list[dict[str, Any]] = []
    if system:
        transcript.append({"role": "system", "content": system})
    transcript.extend(dict(message) for message in messages)
    return transcript


def generation_output(response: Any) -> dict[str, Any]:
    """The assistant turn the model produced, tool calls in OpenAI function shape."""
    payload: dict[str, Any] = {
        "role": "assistant",
        "content": str(getattr(response, "content", "") or ""),
    }
    tool_calls: Iterable[Any] = getattr(response, "tool_calls", None) or ()
    cards = [_tool_call_card(tool_call) for tool_call in tool_calls]
    if cards:
        payload["tool_calls"] = cards
    return payload


def tool_names(tools: Iterable[Any]) -> list[str]:
    """Stable, sorted tool names for agent-level metadata."""
    return sorted(str(getattr(tool, "name", "tool")) for tool in tools)


__all__ = ["generation_input", "generation_output", "tool_names"]
