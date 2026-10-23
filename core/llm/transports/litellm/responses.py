"""ChatGPT plan inference using LiteLLM's public Responses interface."""

from __future__ import annotations

import warnings
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import closing
from typing import Any, cast

from openai import OpenAIError
from pydantic import BaseModel

from core.context_budget import strip_internal_message_markers
from core.llm.shared.openai_chat_completions import (
    build_assistant_message,
    build_tool_result_message,
    build_tool_result_messages,
    get_attr_or_item,
    normalize_messages_openai,
)
from core.llm.shared.openai_responses import (
    response_raw_message,
    response_tool_calls,
    responses_input,
    responses_tool_specs,
)
from core.llm.shared.structured_output import StructuredOutputClient
from core.llm.shared.tool_schema_normalize import build_openai_tool_specs
from core.llm.shared.usage import (
    coerce_usage_tokens,
    emit_usage,
    extract_cache_tokens,
    extract_reasoning_tokens,
)
from core.llm.subscriptions.chatgpt import resolve
from core.llm.types import (
    AgentLLMResponse,
    CancelProbe,
    LLMRequestCancelled,
    LLMResponse,
    LLMStreamInterrupted,
    SchemaDescribedTool,
    TextDeltaCallback,
)


def _text(response: Any) -> str:
    return "".join(
        str(get_attr_or_item(part, "text") or "")
        for item in get_attr_or_item(response, "output") or []
        if get_attr_or_item(item, "type") == "message"
        for part in get_attr_or_item(item, "content") or []
        if get_attr_or_item(part, "type") == "output_text"
    )


def _stream_error(event: Any) -> RuntimeError:
    response = get_attr_or_item(event, "response")
    error = get_attr_or_item(response, "error") or get_attr_or_item(event, "error") or event
    code = get_attr_or_item(error, "code")
    if code in {
        "subscription_sharing_usage_limit_exceeded",
        "subscription_sharing_usage_unavailable",
    }:
        return RuntimeError(
            "ChatGPT plan usage is unavailable or its limit was reached. Review ChatGPT Settings → Usage, then retry."
        )
    return RuntimeError(
        "ChatGPT inference failed or was incomplete. Retry the request; verify access with `opensre auth verify chatgpt`."
    )


def _raise_if_stopped(should_stop: CancelProbe | None) -> None:
    if should_stop is not None and should_stop():
        raise LLMRequestCancelled("ChatGPT request cancelled by the caller")


class ChatGPTResponsesAgentClient:
    """Stateless tool turns with request-scoped OAuth and no API-key fallback."""

    provider_name = "ChatGPT subscription"
    auth_error_hint = "Run `opensre auth login chatgpt`."
    build_tool_result_messages = staticmethod(build_tool_result_messages)
    build_tool_result_message = staticmethod(build_tool_result_message)
    build_assistant_message = staticmethod(build_assistant_message)

    def __init__(
        self,
        model: str,
        *,
        usage_callback: Callable[..., object] = emit_usage,
    ) -> None:
        self.model_id = model
        self._usage_callback = usage_callback

    def tool_schemas(self, tools: Sequence[SchemaDescribedTool]) -> list[dict[str, Any]]:
        return build_openai_tool_specs(tools)

    def _events(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        should_stop: CancelProbe | None = None,
    ) -> Generator[Any]:
        _raise_if_stopped(should_stop)
        from litellm import responses

        # LiteLLM's background logging copy replaces typed Responses usage with a dict.
        # Its worker outlives iteration, so filter only this known dependency warning.
        warnings.filterwarnings(
            "ignore",
            message=r"Pydantic serializer warnings:\n  PydanticSerializationUnexpectedValue\(Expected `ResponseAPIUsage`",
            category=UserWarning,
            module=r"pydantic\.main",
        )
        auth = resolve()
        kwargs: dict[str, Any] = {
            "model": f"openai/{self.model_id}",
            "api_key": auth.token,
            "api_base": auth.base_url,
            "input": responses_input(strip_internal_message_markers(messages)),
            "store": False,
            "stream": True,
            "num_retries": 0,
            "timeout": 120,
            "include": ["reasoning.encrypted_content"],
        }
        if system:
            kwargs["instructions"] = system
        if tools:
            kwargs["input"].insert(
                0,
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": responses_tool_specs(tools),
                },
            )
        stream = None
        completed = False
        try:
            _raise_if_stopped(should_stop)
            # LiteLLM's return annotation does not narrow for synchronous stream=True.
            stream = cast(Iterator[Any], responses(**kwargs))
            for event in stream:
                _raise_if_stopped(should_stop)
                kind = get_attr_or_item(event, "type")
                if kind in {"response.failed", "response.incomplete", "error"}:
                    raise _stream_error(event)
                if kind == "response.completed":
                    completed = True
                yield event
                # A text callback can cancel the turn; do not wait for another SSE event.
                _raise_if_stopped(should_stop)
            if not completed:
                raise RuntimeError(
                    "ChatGPT stream ended before response.completed. Retry the request."
                )
        except OpenAIError as exc:
            original = getattr(exc, "original_exception", None) or exc
            body = getattr(original, "body", None) or {}
            raise _stream_error({"error": body}) from None
        finally:
            # LiteLLM's sync Responses iterator has no close(); it owns an HTTP response.
            response = getattr(stream, "response", stream)
            close = getattr(response, "close", None)
            if close is not None:
                close()

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        on_text_delta: TextDeltaCallback | None = None,
        should_stop: CancelProbe | None = None,
    ) -> AgentLLMResponse:
        # Live text is separate from the completed payload used for reasoning/tool replay.
        response: Any = None
        output_items: dict[int, Any] = {}
        delivered: list[str] = []
        try:
            with closing(
                self._events(messages, system=system, tools=tools, should_stop=should_stop)
            ) as events:
                for event in events:
                    kind = get_attr_or_item(event, "type")
                    if kind == "response.output_text.delta" and on_text_delta is not None:
                        delta = str(get_attr_or_item(event, "delta") or "")
                        if delta:
                            delivered.append(delta)
                            on_text_delta(delta)
                    elif kind == "response.output_item.done":
                        output_items[int(get_attr_or_item(event, "output_index"))] = (
                            get_attr_or_item(event, "item")
                        )
                    elif kind == "response.completed":
                        response = get_attr_or_item(event, "response")
            if response is None:
                raise RuntimeError("ChatGPT did not return a completed response.")
        except LLMRequestCancelled:
            raise
        except Exception as exc:
            if not delivered:
                raise
            raise LLMStreamInterrupted(
                f"ChatGPT response stream failed after output began: {exc}",
                partial_text="".join(delivered),
            ) from exc
        # Plan streams can omit output from the terminal event; done items are canonical.
        if not get_attr_or_item(response, "output") and output_items:
            output = [output_items[index] for index in sorted(output_items)]
            if isinstance(response, dict):
                response = {**response, "output": output}
            else:
                response = response.model_copy(update={"output": output})
        calls = response_tool_calls(response)
        usage = get_attr_or_item(response, "usage")
        inp, out = coerce_usage_tokens(usage, input_key="input_tokens", output_key="output_tokens")
        cache_read, cache_write = extract_cache_tokens(usage)
        self._usage_callback(self.model_id, inp, out)
        raw = response_raw_message(response)
        raw["content"] = content = _text(response)
        return AgentLLMResponse(
            content=content,
            tool_calls=calls,
            stop_reason="tool_use" if calls else "end_turn",
            raw_content=raw,
            input_tokens=inp,
            output_tokens=out,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_write,
            reasoning_tokens=extract_reasoning_tokens(usage),
        )


class ChatGPTResponsesLLMClient:
    """Reasoning and structured output over the same subscription connection."""

    def __init__(
        self,
        model: str,
        *,
        usage_callback: Callable[..., object] | None = None,
    ) -> None:
        self._agent = ChatGPTResponsesAgentClient(
            model, usage_callback=usage_callback or emit_usage
        )

    def with_config(self, **_kwargs: Any) -> ChatGPTResponsesLLMClient:
        return self

    def with_structured_output(self, model: type[BaseModel]) -> StructuredOutputClient:
        return StructuredOutputClient(self, model)

    @staticmethod
    def _messages(prompt: Any) -> list[dict[str, Any]]:
        from infrastructure.safety.guardrails.apply import apply_guardrails_to_messages

        messages, _ = apply_guardrails_to_messages(normalize_messages_openai(prompt))
        return messages

    def invoke(self, prompt_or_messages: Any) -> LLMResponse:
        result = self._agent.invoke(self._messages(prompt_or_messages))
        return LLMResponse(
            content=result.content,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_creation_tokens=result.cache_creation_tokens,
        )

    def invoke_stream(self, prompt_or_messages: Any) -> Iterator[str]:
        for event in self._agent._events(self._messages(prompt_or_messages)):
            kind = get_attr_or_item(event, "type")
            if kind == "response.output_text.delta":
                yield str(get_attr_or_item(event, "delta") or "")
            elif kind == "response.completed":
                usage = get_attr_or_item(get_attr_or_item(event, "response"), "usage")
                inp, out = coerce_usage_tokens(
                    usage, input_key="input_tokens", output_key="output_tokens"
                )
                self._agent._usage_callback(self._agent.model_id, inp, out)
