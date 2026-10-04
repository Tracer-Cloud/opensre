"""OpenAI Responses API request and response adapters."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Any

from config.constants.llm import (
    OPENAI_PROMPT_CACHE_KEY_HASH_CHARS,
    OPENAI_PROMPT_CACHE_KEY_PREFIX,
)
from core.llm.types import ToolCall

_RESPONSE_OUTPUT_KEY = "_openai_response_output"


def uses_responses_api(model: str, api_key_env: str) -> bool:
    """Return whether this official OpenAI model requires the Responses API."""
    return api_key_env == "OPENAI_API_KEY" and model.lower().startswith("gpt-5.6")


@lru_cache(maxsize=32)
def responses_prompt_cache_key(system: str) -> str:
    """Return the ``prompt_cache_key`` for requests that share this system prompt.

    The key is derived from the prompt itself, so every request that can reuse
    the same cached prefix carries the same key, and different prompts never
    share a routing bucket. Cached because one run sends the same prompt on
    every model call.
    """
    digest = hashlib.sha256(system.encode("utf-8")).hexdigest()
    return f"{OPENAI_PROMPT_CACHE_KEY_PREFIX}{digest[:OPENAI_PROMPT_CACHE_KEY_HASH_CHARS]}"


def responses_tool_specs(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert externally tagged Chat Completions tools to Responses tools."""
    converted: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function")
        if tool.get("type") != "function" or not isinstance(function, dict):
            converted.append(dict(tool))
            continue
        converted.append(
            {
                "type": "function",
                "name": function.get("name", ""),
                "description": function.get("description", ""),
                "parameters": function.get("parameters", {}),
                "strict": function.get("strict", False),
            }
        )
    return converted


def responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert the runtime's Chat-shaped transcript to Responses input items."""
    items: list[dict[str, Any]] = []
    for message in messages:
        response_output = message.get(_RESPONSE_OUTPUT_KEY)
        if isinstance(response_output, list):
            items.extend(dict(item) for item in response_output if isinstance(item, dict))
            continue

        role = message.get("role")
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id", "")),
                    "output": str(message.get("content", "")),
                }
            )
            continue

        content = message.get("content", "")
        if role == "assistant" and message.get("tool_calls"):
            if content:
                items.append({"role": "assistant", "content": str(content)})
            for tool_call in message["tool_calls"]:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function", {})
                if not isinstance(function, dict):
                    continue
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(tool_call.get("id", "")),
                        "name": str(function.get("name", "")),
                        "arguments": str(function.get("arguments", "{}")),
                    }
                )
            continue

        items.append({"role": str(role or "user"), "content": str(content)})
    return items


def response_output_items(response: Any) -> list[dict[str, Any]]:
    """Serialize Responses output items for stateless replay on the next turn."""
    serialized: list[dict[str, Any]] = []
    for item in getattr(response, "output", []) or []:
        if hasattr(item, "model_dump"):
            serialized.append(item.model_dump(exclude_none=True))
        elif isinstance(item, dict):
            serialized.append(dict(item))
    return serialized


def response_tool_calls(response: Any) -> list[ToolCall]:
    """Extract function calls from a Responses API result."""
    tool_calls: list[ToolCall] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "function_call":
            continue
        raw_arguments = str(getattr(item, "arguments", "") or "")
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
        except (json.JSONDecodeError, ValueError):
            arguments = {}
        tool_calls.append(
            ToolCall(
                id=str(getattr(item, "call_id", "")),
                name=str(getattr(item, "name", "")),
                input=arguments if isinstance(arguments, dict) else {},
            )
        )
    return tool_calls


def response_raw_message(response: Any) -> dict[str, Any]:
    """Build the replayable assistant message stored in the runtime transcript."""
    message: dict[str, Any] = {
        "role": "assistant",
        "content": str(getattr(response, "output_text", "") or ""),
        _RESPONSE_OUTPUT_KEY: response_output_items(response),
    }
    tool_calls = response_tool_calls(response)
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.name,
                    "arguments": json.dumps(tool_call.input),
                },
            }
            for tool_call in tool_calls
        ]
    return message
