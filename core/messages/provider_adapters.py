"""Per-provider message-shaping adapters for :class:`MessageMapper`.

Each provider (Anthropic, Bedrock Converse, OpenAI-family, CLI-backed) shapes
assistant messages, tool results, and synthetic tool-call turns differently.
Rather than scatter ``isinstance`` ladders across the mapper, one adapter per
provider owns its shapes, and :func:`adapter_for` maps a client to its adapter in
a single place.

Typing: the provider clients expose two distinct ``build_assistant_message``
shapes — Anthropic/Bedrock echo the provider's raw content (``raw_content``),
while OpenAI/CLI construct from ``content`` + ``tool_calls``. Each adapter is
generic over the narrow client Protocol it needs (:class:`_RawAssistantClient`,
:class:`_ConstructAssistantClient`, :class:`_OpenAIShapedClient`), so calls are
checked against the real client rather than ``Any``.
"""

from __future__ import annotations

import sys
from typing import Any, Protocol

from config.constants.llm import ANTHROPIC_THINKING_BINDING_KEY
from core.llm.types import AgentLLMResponse, ToolCall
from core.messages.runtime_message_types import ProviderMessage, RuntimeContent

#: Defines every SDK-backed agent client :func:`adapter_for` dispatches on.
_SDK_AGENT_CLIENTS_MODULE = "core.llm.transports.sdk.agent_clients"


class _ToolResultClient(Protocol):
    """The tool-result surface every provider client shares."""

    def build_tool_result_message(
        self, tool_calls: list[ToolCall], results: list[Any]
    ) -> dict[str, Any]:
        """Build one provider tool-result message for a batch of calls."""


class _RawAssistantClient(_ToolResultClient, Protocol):
    """Clients that rebuild an assistant message from the provider's raw content."""

    def build_assistant_message(self, raw_content: Any) -> dict[str, Any]:
        """Rebuild the assistant message from the provider's raw response content."""


class _ConstructAssistantClient(_ToolResultClient, Protocol):
    """Clients that construct an assistant message from text + tool calls."""

    def build_assistant_message(self, content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        """Construct the assistant message from text content and tool calls."""


class _OpenAIShapedClient(_ConstructAssistantClient, Protocol):
    """OpenAI-family clients: one assistant message but many tool-result messages."""

    def build_tool_result_messages(
        self, tool_calls: list[ToolCall], results: list[Any]
    ) -> list[dict[str, Any]]:
        """Build one provider tool-result message per tool call."""


class MessageAdapter[LLMT: _ToolResultClient]:
    """Base shapes shared by every provider; ``to_assistant_provider_message`` is provider-specific."""

    def __init__(self, llm: LLMT) -> None:
        self._llm = llm

    def to_assistant_provider_message(self, response: AgentLLMResponse) -> ProviderMessage:
        raise NotImplementedError

    def to_tool_result_provider_messages(
        self, tool_calls: list[ToolCall], results: list[Any]
    ) -> list[ProviderMessage]:
        return [self._llm.build_tool_result_message(tool_calls, results)]

    def to_synthetic_assistant_provider_message(
        self, tool_calls: list[ToolCall]
    ) -> ProviderMessage:
        names = ", ".join(tc.name for tc in tool_calls)
        return {"role": "assistant", "content": f"I will start by querying: {names}"}

    def to_replayed_assistant_provider_message(
        self, content: str, tool_calls: list[ToolCall]
    ) -> ProviderMessage:
        """An assistant turn rebuilt from stored text and tool calls, with no raw payload.

        Replayed history from an earlier turn arrives this way when its provider
        message was not recorded or belongs to another client.
        """
        raise NotImplementedError

    def app_message_content(self, content: RuntimeContent) -> RuntimeContent:
        return content

    def client_markers(self) -> frozenset[str]:
        """Internal ``_opensre_*`` message keys the provider client reads, so they reach it."""
        return frozenset()


class _GenericAdapter[LLMT: _ConstructAssistantClient](MessageAdapter[LLMT]):
    """OpenAI-family / unknown clients that construct assistant messages from text."""

    def to_assistant_provider_message(self, response: AgentLLMResponse) -> ProviderMessage:
        # raw_content carries provider-specific extras (e.g. Gemini's
        # thought_signature) that must be echoed back verbatim next request.
        if response.raw_content is not None:
            return response.raw_content  # type: ignore[no-any-return]
        return self._llm.build_assistant_message(response.content, response.tool_calls)

    def to_replayed_assistant_provider_message(
        self, content: str, tool_calls: list[ToolCall]
    ) -> ProviderMessage:
        return self._llm.build_assistant_message(content, tool_calls)


class _AnthropicAdapter[LLMT: _RawAssistantClient](MessageAdapter[LLMT]):
    def to_assistant_provider_message(self, response: AgentLLMResponse) -> ProviderMessage:
        return self._llm.build_assistant_message(response.raw_content)

    def to_synthetic_assistant_provider_message(
        self, tool_calls: list[ToolCall]
    ) -> ProviderMessage:
        return {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
                for tc in tool_calls
            ],
        }

    def to_replayed_assistant_provider_message(
        self, content: str, tool_calls: list[ToolCall]
    ) -> ProviderMessage:
        # The block builders the live turn's message used, so a replayed turn
        # matches the prefix that request cached.
        from core.llm.transports.sdk.anthropic_cache import text_block, tool_use_block

        if not tool_calls:
            return {"role": "assistant", "content": content}
        # Anthropic rejects empty text blocks, so text rides only when present.
        blocks: list[dict[str, Any]] = [text_block(content)] if content else []
        blocks.extend(tool_use_block(tc.id, tc.name, tc.input) for tc in tool_calls)
        return {"role": "assistant", "content": blocks}

    def client_markers(self) -> frozenset[str]:
        # The client checks each resent thinking block against its binding.
        return frozenset({ANTHROPIC_THINKING_BINDING_KEY})


class _BedrockConverseAdapter[LLMT: _RawAssistantClient](MessageAdapter[LLMT]):
    def to_assistant_provider_message(self, response: AgentLLMResponse) -> ProviderMessage:
        return self._llm.build_assistant_message(response.raw_content)

    def to_synthetic_assistant_provider_message(
        self, tool_calls: list[ToolCall]
    ) -> ProviderMessage:
        from core.llm.transports.sdk.bedrock_converse import build_assistant_tool_use_message

        result: dict[str, Any] = build_assistant_tool_use_message(tool_calls)
        return result

    def to_replayed_assistant_provider_message(
        self, content: str, tool_calls: list[ToolCall]
    ) -> ProviderMessage:
        from core.llm.transports.sdk.bedrock_converse import build_assistant_tool_use_message

        message: dict[str, Any] = build_assistant_tool_use_message(tool_calls)
        text_blocks: list[dict[str, Any]] = [{"text": content}] if content else []
        message["content"] = text_blocks + list(message["content"])
        return message

    def app_message_content(self, content: RuntimeContent) -> RuntimeContent:
        return _to_converse_text_blocks(content)


class _OpenAIAdapter[LLMT: _OpenAIShapedClient](_GenericAdapter[LLMT]):
    def to_tool_result_provider_messages(
        self, tool_calls: list[ToolCall], results: list[Any]
    ) -> list[ProviderMessage]:
        return list(self._llm.build_tool_result_messages(tool_calls, results))

    def to_synthetic_assistant_provider_message(
        self, tool_calls: list[ToolCall]
    ) -> ProviderMessage:
        # Reuse the canonical OpenAI tool_calls shape, then restore the
        # synthetic-turn convention of a null (not empty-string) content.
        from core.llm.shared.openai_chat_completions import build_assistant_message

        message = build_assistant_message("", tool_calls)
        message["content"] = None
        return message


class _CLIAdapter[LLMT: _ConstructAssistantClient](_GenericAdapter[LLMT]):
    def to_synthetic_assistant_provider_message(
        self, tool_calls: list[ToolCall]
    ) -> ProviderMessage:
        return self._llm.build_assistant_message("", tool_calls)


def adapter_for(llm: Any) -> MessageAdapter[Any]:
    """Resolve the message adapter for a provider client (the one dispatch point)."""
    # No SDK client can exist before its module is loaded, and loading it imports
    # the provider SDKs: a static tool-call turn must not pay that just to learn
    # it holds none.
    if _SDK_AGENT_CLIENTS_MODULE in sys.modules:
        from core.llm.transports.sdk.agent_clients import (
            AnthropicAgentClient,
            BedrockConverseAgentClient,
            CLIBackedAgentClient,
            OpenAIAgentClient,
        )

        if isinstance(llm, BedrockConverseAgentClient):
            return _BedrockConverseAdapter(llm)
        if isinstance(llm, AnthropicAgentClient):
            return _AnthropicAdapter(llm)
        if isinstance(llm, OpenAIAgentClient):
            return _OpenAIAdapter(llm)
        if isinstance(llm, CLIBackedAgentClient):
            return _CLIAdapter(llm)
    if _is_litellm_agent_client(llm):
        return _OpenAIAdapter(llm)
    return _GenericAdapter(llm)


def _is_litellm_agent_client(llm: Any) -> bool:
    cls = type(llm)
    return (cls.__module__, cls.__name__) in {
        ("core.llm.transports.litellm.clients", "LiteLLMAgentClient"),
        ("core.llm.transports.litellm.responses", "ChatGPTResponsesAgentClient"),
    }


def _to_converse_text_blocks(content: RuntimeContent) -> RuntimeContent:
    if not isinstance(content, list):
        return content
    converted: list[dict[str, Any]] = []
    for block in content:
        if block.get("type") == "text" and "text" in block:
            converted.append({"text": str(block["text"])})
        else:
            converted.append(dict(block))
    return converted


__all__ = ["MessageAdapter", "adapter_for"]
