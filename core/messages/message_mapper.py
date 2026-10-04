"""Single boundary for all runtime <-> provider message conversion."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from core.context_budget import strip_internal_message_markers
from core.llm.types import AgentLLMResponse, ToolCall
from core.messages.provider_adapters import adapter_for
from core.messages.runtime_message_types import (
    AppRuntimeMessage,
    AssistantRuntimeMessage,
    MessageMetadata,
    ProviderMessage,
    RuntimeContent,
    RuntimeMessage,
    RuntimeMessageLike,
    ToolResultRuntimeMessage,
    UserRuntimeMessage,
)


class MessageMapper:
    """Converts runtime messages to/from provider-specific dicts for LLM invocation.

    ``to_runtime_messages`` is a staticmethod — no llm needed.
    All other methods require an llm instance.
    """

    def __init__(self, llm: Any) -> None:
        self._llm = llm
        # Resolve the provider dispatch once — the llm is fixed for this mapper's lifetime.
        self._adapter = adapter_for(llm)

    @staticmethod
    def to_runtime_messages(messages: Sequence[RuntimeMessageLike]) -> list[RuntimeMessage]:
        """Convert legacy provider dicts and typed messages into RuntimeMessage objects."""
        return [_to_runtime_message(m) for m in messages]

    def to_provider_messages(self, messages: Sequence[RuntimeMessage]) -> list[ProviderMessage]:
        """Render a RuntimeMessage sequence into provider dicts for llm.invoke.

        ``provider_payload``/``provider_payloads`` on a coerced RuntimeMessage retain
        internal ``_opensre_*`` markers (see ``_metadata_from_provider_message``), so
        the outbound render is stripped here rather than trusting each producer.
        """
        result: list[ProviderMessage] = []
        for message in messages:
            result.extend(self._for_runtime_message(message))
        return strip_internal_message_markers(result)

    def to_assistant_provider_message(self, response: AgentLLMResponse) -> ProviderMessage:
        """Build the provider assistant-message payload from an LLM response."""
        return self._adapter.to_assistant_provider_message(response)

    def to_tool_result_provider_messages(
        self,
        tool_calls: list[ToolCall],
        results: list[Any],
    ) -> list[ProviderMessage]:
        """Build provider tool-result payloads for a batch of tool calls."""
        return self._adapter.to_tool_result_provider_messages(tool_calls, results)

    def to_synthetic_assistant_provider_message(
        self, tool_calls: list[ToolCall]
    ) -> ProviderMessage:
        """Build a synthetic assistant message that looks like the LLM requested these tool calls.

        Used to inject pre-seeded tool results into the conversation without special-casing.
        """
        return self._adapter.to_synthetic_assistant_provider_message(tool_calls)

    def to_assistant_runtime_message(self, response: AgentLLMResponse) -> AssistantRuntimeMessage:
        """Build a typed assistant transcript entry from an LLM response."""
        return AssistantRuntimeMessage(
            content=response.content or "",
            tool_calls=tuple(response.tool_calls),
            provider_payload=self.to_assistant_provider_message(response),
        )

    def to_tool_result_runtime_message(
        self,
        tool_calls: list[ToolCall],
        results: list[Any],
    ) -> ToolResultRuntimeMessage:
        """Build a typed tool-result transcript entry from executed tool calls."""
        return ToolResultRuntimeMessage(
            tool_calls=tuple(tool_calls),
            results=tuple(results),
            provider_payloads=tuple(self.to_tool_result_provider_messages(tool_calls, results)),
        )

    def _for_runtime_message(self, message: RuntimeMessage) -> list[ProviderMessage]:
        if isinstance(message, UserRuntimeMessage):
            return [{"role": "user", "content": message.content}]
        if isinstance(message, AssistantRuntimeMessage):
            if message.provider_payload is not None:
                return [dict(message.provider_payload)]
            content = message.content
            text = content if isinstance(content, str) else json.dumps(content, default=str)
            return [
                self._adapter.to_replayed_assistant_provider_message(
                    text if content is not None else "", list(message.tool_calls)
                )
            ]
        if isinstance(message, ToolResultRuntimeMessage):
            if message.provider_payloads:
                return [dict(payload) for payload in message.provider_payloads]
            return self.to_tool_result_provider_messages(
                list(message.tool_calls), list(message.results)
            )
        if isinstance(message, AppRuntimeMessage):
            if not message.include_in_context:
                return []
            return [{"role": "user", "content": self._app_message_content(message)}]
        return []

    def _app_message_content(self, message: AppRuntimeMessage) -> RuntimeContent:
        return self._adapter.app_message_content(message.content)


def _to_runtime_message(message: RuntimeMessageLike) -> RuntimeMessage:
    if not isinstance(message, dict):
        return message

    role = message.get("role")
    if role == "user":
        return UserRuntimeMessage(
            content=message.get("content"),
            metadata=_metadata_from_provider_message(message),
        )
    if role == "assistant":
        return AssistantRuntimeMessage(
            content=message.get("content"),
            provider_payload=dict(message),
            metadata=_metadata_from_provider_message(message),
        )
    # One tool-result turn, however the provider spelled the role:
    # OpenAI "tool", Bedrock "toolResult", snake-case "tool_result".
    if role in {"tool", "toolResult", "tool_result"}:
        # Field names likewise vary by provider: snake_case (OpenAI/Anthropic) vs camelCase (Bedrock).
        tool_name = str(message.get("name") or message.get("toolName") or "tool")
        tool_call_id = str(message.get("tool_call_id") or message.get("toolCallId") or tool_name)
        tool_call = ToolCall(id=tool_call_id, name=tool_name, input={})
        return ToolResultRuntimeMessage(
            tool_calls=(tool_call,),
            results=(message.get("content"),),
            provider_payloads=(dict(message),),
            metadata=_metadata_from_provider_message(message),
        )
    return AppRuntimeMessage(
        app_type="provider_message",
        content=json.dumps(message, default=str),
        include_in_context=False,
        details=dict(message),
        metadata=_metadata_from_provider_message(message),
    )


def _metadata_from_provider_message(message: ProviderMessage) -> MessageMetadata:
    return {key: value for key, value in message.items() if key.startswith("_opensre_")}


__all__ = ["MessageMapper"]
