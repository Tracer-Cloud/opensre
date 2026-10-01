"""LiteLLM-backed agent and non-agent LLM clients."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from core.llm.transports.litellm.frozen_tiktoken_bootstrap import (
    ensure_tiktoken_encodings_discoverable,
)

ensure_tiktoken_encodings_discoverable()

from litellm import completion  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from core.context_budget import strip_internal_message_markers  # noqa: E402
from core.llm.shared.openai_chat_completions import (  # noqa: E402
    AGENT_CLIENT_TIMEOUT_SEC,
    LLM_CLIENT_TIMEOUT_SEC,
    agent_response_from_completion,
    build_assistant_message,
    build_tool_result_message,
    build_tool_result_messages,
    invoke_with_litellm_agent_retries,
    invoke_with_litellm_llm_retries,
    llm_response_from_completion,
    normalize_messages_openai,
    prepend_system_message,
    stream_with_litellm_retries,
)
from core.llm.shared.structured_output import StructuredOutputClient  # noqa: E402
from core.llm.shared.tool_schema_normalize import build_openai_tool_specs  # noqa: E402
from core.llm.types import (  # noqa: E402
    AgentLLMResponse,
    LLMResponse,
    SchemaDescribedTool,
    ToolCall,
)

logger = logging.getLogger(__name__)


class LiteLLMAgentClient:
    """LiteLLM-backed tool-calling client for the agent ReAct loop."""

    provider_name = "LiteLLM"
    auth_error_hint = "Check the provider API key configured for this model."

    build_tool_result_messages = staticmethod(build_tool_result_messages)
    build_assistant_message = staticmethod(build_assistant_message)

    def __init__(
        self,
        *,
        litellm_model: str,
        max_tokens: int = 4096,
        api_base: str | None = None,
        api_version: str | None = None,
        api_key_env: str | None = None,
        api_key_default: str = "",
        temperature: float | None = None,
        vertex_project: str | None = None,
        vertex_location: str | None = None,
        credential_resolver: Callable[[str], str] | None = None,
        completion_func: Callable[..., Any] | None = None,
    ) -> None:
        self._litellm_model = litellm_model
        self._max_tokens = max_tokens
        self._api_base = api_base
        self._api_version = api_version
        self._api_key_env = api_key_env
        self._api_key_default = api_key_default
        self._temperature = temperature
        self._vertex_project = vertex_project
        self._vertex_location = vertex_location
        self._credential_resolver = credential_resolver
        self._completion_func = completion_func

    @property
    def model_id(self) -> str | None:
        return self._litellm_model

    def tool_schemas(self, tools: Sequence[SchemaDescribedTool]) -> list[dict[str, Any]]:
        return build_openai_tool_specs(tools)

    def _completion(self, **kwargs: Any) -> Any:
        if self._completion_func is not None:
            return self._completion_func(**kwargs)
        return completion(**kwargs)

    def _api_key(self) -> str | None:
        if self._api_key_env is None:
            return None
        if self._credential_resolver is not None:
            return self._credential_resolver(self._api_key_env) or self._api_key_default
        from config.llm_credentials import resolve_env_credential

        return resolve_env_credential(self._api_key_env) or self._api_key_default

    def _build_request_kwargs(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._litellm_model,
            "messages": prepend_system_message(strip_internal_message_markers(messages), system),
            "max_tokens": self._max_tokens,
            "timeout": AGENT_CLIENT_TIMEOUT_SEC,
        }
        api_key = self._api_key()
        if api_key is not None:
            kwargs["api_key"] = api_key
        if self._api_base is not None:
            kwargs["api_base"] = self._api_base
        if self._api_version is not None:
            kwargs["api_version"] = self._api_version
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature
        if self._vertex_project is not None:
            kwargs["vertex_project"] = self._vertex_project
        if self._vertex_location is not None:
            kwargs["vertex_location"] = self._vertex_location
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return kwargs

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        kwargs = self._build_request_kwargs(messages, system=system, tools=tools)
        response = invoke_with_litellm_agent_retries(
            self._completion,
            kwargs,
            provider_name=self.provider_name,
            model=self._litellm_model,
        )
        return agent_response_from_completion(
            response,
            provider_name=self.provider_name,
            model=self._litellm_model,
        )

    @staticmethod
    def build_tool_result_message(tool_calls: list[ToolCall], results: list[Any]) -> dict[str, Any]:
        return build_tool_result_message(tool_calls, results)


class LiteLLMLLMClient:
    """LiteLLM-backed non-agent LLM client for reasoning and classification."""

    def __init__(
        self,
        *,
        litellm_model: str,
        model_fallback: str | None = None,
        max_tokens: int = 1024,
        temperature: float | None = None,
        api_base: str | None = None,
        api_version: str | None = None,
        api_key_env: str | None = None,
        api_key_default: str = "",
        vertex_project: str | None = None,
        vertex_location: str | None = None,
        credential_resolver: Callable[[str], str] | None = None,
        completion_func: Callable[..., Any] | None = None,
        usage_callback: Callable[[str, int | None, int | None], object] | None = None,
    ) -> None:
        self._litellm_model = litellm_model
        fallback = (model_fallback or "").strip()
        self._model_fallback = fallback if fallback and fallback != litellm_model else None
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._api_base = api_base
        self._api_version = api_version
        self._api_key_env = api_key_env
        self._api_key_default = api_key_default
        self._vertex_project = vertex_project
        self._vertex_location = vertex_location
        self._credential_resolver = credential_resolver
        self._completion_func = completion_func
        self._usage_callback = usage_callback
        label = (api_key_env or "").removesuffix("_API_KEY").replace("_", " ").title()
        self._provider_label = label or "LiteLLM"

    def with_config(self, **_kwargs: Any) -> LiteLLMLLMClient:
        return self

    def with_structured_output(self, model: type[BaseModel]) -> StructuredOutputClient:
        return StructuredOutputClient(self, model)

    def _completion(self, **kwargs: Any) -> Any:
        if self._completion_func is not None:
            return self._completion_func(**kwargs)
        return completion(**kwargs)

    def _api_key(self) -> str | None:
        if self._api_key_env is None:
            return None
        if self._credential_resolver is not None:
            return self._credential_resolver(self._api_key_env) or self._api_key_default
        from config.llm_credentials import resolve_env_credential

        return resolve_env_credential(self._api_key_env) or self._api_key_default

    def _activate_model_fallback(self) -> bool:
        fallback = self._model_fallback
        if not fallback or fallback == self._litellm_model:
            return False
        previous = self._litellm_model
        self._litellm_model = fallback
        logger.warning(
            "%s model '%s' unavailable; falling back to toolcall model '%s'.",
            self._provider_label,
            previous,
            fallback,
        )
        return True

    def _build_request_kwargs(self, prompt_or_messages: Any) -> dict[str, Any]:
        from infrastructure.safety.guardrails.apply import apply_guardrails_to_messages

        # normalize_messages_openai already keeps only role/content, but strip explicitly
        # so this stays safe if a future caller ever routes marked agent-history dicts here.
        messages = strip_internal_message_markers(normalize_messages_openai(prompt_or_messages))
        messages, _ = apply_guardrails_to_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self._litellm_model,
            "messages": messages,
            "max_tokens": self._max_tokens,
            "timeout": LLM_CLIENT_TIMEOUT_SEC,
        }
        api_key = self._api_key()
        if api_key is not None:
            kwargs["api_key"] = api_key
        if self._api_base is not None:
            kwargs["api_base"] = self._api_base
        if self._api_version is not None:
            kwargs["api_version"] = self._api_version
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature
        if self._vertex_project is not None:
            kwargs["vertex_project"] = self._vertex_project
        if self._vertex_location is not None:
            kwargs["vertex_location"] = self._vertex_location
        return kwargs

    def _rebuild_after_model_fallback(self, prompt_or_messages: Any) -> dict[str, Any] | None:
        if not self._activate_model_fallback():
            return None
        return self._build_request_kwargs(prompt_or_messages)

    def invoke(self, prompt_or_messages: Any) -> LLMResponse:
        response = invoke_with_litellm_llm_retries(
            self._completion,
            self._build_request_kwargs(prompt_or_messages),
            provider_label=self._provider_label,
            api_key_env=self._api_key_env or "",
            model=self._litellm_model,
            on_model_fallback=lambda: self._rebuild_after_model_fallback(prompt_or_messages),
        )
        return llm_response_from_completion(
            response,
            model=self._litellm_model,
            bound_tools=False,
            usage_emit=self._usage_callback,
        )

    def invoke_stream(self, prompt_or_messages: Any) -> Iterator[str]:
        yield from stream_with_litellm_retries(
            self._completion,
            self._build_request_kwargs(prompt_or_messages),
            provider_label=self._provider_label,
            api_key_env=self._api_key_env or "",
            model=self._litellm_model,
            on_model_fallback=lambda: self._rebuild_after_model_fallback(prompt_or_messages),
        )
