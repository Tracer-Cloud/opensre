"""OpenAI Chat Completions helpers: messages, responses, and retries."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from http import HTTPStatus
from typing import Any

from core.llm.shared.llm_retry import (
    extract_retry_after_seconds,
    maybe_raise_credit_exhausted,
    rate_limit_sleep_seconds,
)
from core.llm.shared.usage import emit_usage
from core.llm.types import AgentLLMResponse, LLMResponse, ToolCall

_RETRY_INITIAL_BACKOFF_SEC = 1.0
_RETRY_MAX_ATTEMPTS = 3

AGENT_CLIENT_TIMEOUT_SEC: float = 90.0
LLM_CLIENT_TIMEOUT_SEC: float = 60.0


def get_attr_or_item(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def first_choice(response: Any) -> Any:
    choices = get_attr_or_item(response, "choices", [])
    if not choices:
        raise RuntimeError("OpenAI-compatible API returned an empty choices list")
    return choices[0]


def message_to_dict(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return {key: value for key, value in message.items() if value is not None}
    model_dump = getattr(message, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(exclude_none=True)
        if isinstance(dumped, dict):
            return {str(key): value for key, value in dumped.items() if value is not None}
    payload: dict[str, Any] = {
        "role": get_attr_or_item(message, "role", "assistant"),
        "content": get_attr_or_item(message, "content", ""),
    }
    tool_calls = get_attr_or_item(message, "tool_calls", None)
    if tool_calls:
        payload["tool_calls"] = tool_calls
    return {key: value for key, value in payload.items() if value is not None}


def parse_tool_calls(message: Any) -> list[ToolCall]:
    tool_calls: list[ToolCall] = []
    for raw_call in get_attr_or_item(message, "tool_calls", None) or []:
        function = get_attr_or_item(raw_call, "function", {})
        call_id = str(get_attr_or_item(raw_call, "id", ""))
        name = str(get_attr_or_item(function, "name", ""))
        raw_arguments = str(get_attr_or_item(function, "arguments", "") or "")
        try:
            input_dict = json.loads(raw_arguments) if raw_arguments else {}
        except json.JSONDecodeError:
            input_dict = {}
        # A model may send arguments as JSON "null" or a non-object (some
        # OpenAI-compatible gateways do for no-arg tools); coerce to {} so
        # downstream consumers that require a dict never receive None. Mirrors
        # the guard in the Responses-API parser (openai_responses.py).
        tool_calls.append(
            ToolCall(
                id=call_id,
                name=name,
                input=input_dict if isinstance(input_dict, dict) else {},
            )
        )
    return tool_calls


def usage_tokens(usage: Any) -> tuple[int | None, int | None]:
    prompt_tokens = get_attr_or_item(usage, "prompt_tokens")
    completion_tokens = get_attr_or_item(usage, "completion_tokens")
    input_tokens = int(prompt_tokens) if isinstance(prompt_tokens, (int, float)) else None
    output_tokens = int(completion_tokens) if isinstance(completion_tokens, (int, float)) else None
    return input_tokens, output_tokens


def normalize_messages_openai(prompt_or_messages: Any) -> list[dict[str, str]]:
    if isinstance(prompt_or_messages, list):
        messages: list[dict[str, str]] = []
        for msg in prompt_or_messages:
            if isinstance(msg, dict):
                role = msg.get("role", "user")
                content = msg.get("content", "")
            else:
                role = getattr(msg, "role", "user")
                content = getattr(msg, "content", "")
            messages.append({"role": str(role), "content": str(content)})
        return messages
    return [{"role": "user", "content": str(prompt_or_messages)}]


def prepend_system_message(
    messages: list[dict[str, Any]],
    system: str | None,
) -> list[dict[str, Any]]:
    msgs = list(messages)
    if system:
        msgs = [{"role": "system", "content": system}] + msgs
    return msgs


def build_tool_result_messages(
    tool_calls: list[ToolCall], results: list[Any]
) -> list[dict[str, Any]]:
    return [
        {
            "role": "tool",
            "tool_call_id": tc.id,
            "content": json.dumps(result, default=str),
        }
        for tc, result in zip(tool_calls, results)
    ]


def build_tool_result_message(tool_calls: list[ToolCall], results: list[Any]) -> dict[str, Any]:
    if len(tool_calls) != 1:
        raise NotImplementedError(
            "OpenAI-compatible tool results must be appended as separate messages"
        )
    return build_tool_result_messages(tool_calls, results)[0]


def build_assistant_message(content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.input)},
            }
            for tc in tool_calls
        ]
    return msg


def agent_response_from_completion(
    response: Any,
    *,
    provider_name: str,
    model: str | None = None,
) -> AgentLLMResponse:
    choices = get_attr_or_item(response, "choices", None)
    if not choices:
        raise RuntimeError(
            f"{provider_name} API returned an unexpected response: {type(response).__name__}"
        )
    choice = choices[0]
    message = get_attr_or_item(choice, "message")
    if message is None:
        raise RuntimeError(
            f"{provider_name} API returned an unexpected response: {type(response).__name__}"
        )
    input_tokens, output_tokens = usage_tokens(get_attr_or_item(response, "usage", None))
    emit_usage(model or provider_name, input_tokens, output_tokens)
    content = str(get_attr_or_item(message, "content", "") or "")
    stop_reason = str(get_attr_or_item(choice, "finish_reason", "stop") or "stop")
    return AgentLLMResponse(
        content=content,
        tool_calls=parse_tool_calls(message),
        stop_reason=stop_reason,
        raw_content=message_to_dict(message),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def llm_content_from_message(message: Any, *, bound_tools: bool) -> str:
    if not bound_tools:
        return str(get_attr_or_item(message, "content", "") or "")
    tool_calls = [
        {"name": call.name, "arguments": call.input} for call in parse_tool_calls(message)
    ]
    if tool_calls:
        return json.dumps(
            {
                "tool_calls": tool_calls,
                "text": str(get_attr_or_item(message, "content", "") or "").strip(),
            },
            ensure_ascii=True,
        )
    return str(get_attr_or_item(message, "content", "") or "").strip()


def llm_response_from_completion(
    response: Any,
    *,
    model: str,
    bound_tools: bool,
    usage_emit: Callable[[str, int | None, int | None], object] | None = None,
) -> LLMResponse:
    message = get_attr_or_item(first_choice(response), "message")
    if message is None:
        raise RuntimeError(
            f"OpenAI-compatible API returned an unexpected response: {type(response).__name__}"
        )
    content = llm_content_from_message(message, bound_tools=bound_tools)
    input_tokens, output_tokens = usage_tokens(get_attr_or_item(response, "usage"))
    if usage_emit is not None and (input_tokens is not None or output_tokens is not None):
        usage_emit(model, input_tokens, output_tokens)
    return LLMResponse(
        content=content.strip(),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def stream_chunk_delta_content(chunk: Any) -> str | None:
    if not get_attr_or_item(chunk, "choices", []):
        return None
    delta = get_attr_or_item(first_choice(chunk), "delta", {})
    content = get_attr_or_item(delta, "content")
    return str(content) if content else None


def is_exception_named(err: BaseException, *names: str) -> bool:
    return type(err).__name__ in names


def _litellm_not_found_message(*, provider_label: str, model: str) -> str:
    from core.llm.providers.azure_openai import (
        format_azure_deployment_not_found_message,
        is_azure_litellm_model,
    )

    if is_azure_litellm_model(model):
        return format_azure_deployment_not_found_message(model)
    return (
        f"{provider_label} model '{model}' was not found. "
        "Check your configured model name or endpoint."
    )


def invoke_with_litellm_agent_retries(
    completion_fn: Callable[..., Any],
    kwargs: dict[str, Any],
    *,
    provider_name: str,
    model: str,
) -> Any:
    backoff = _RETRY_INITIAL_BACKOFF_SEC
    last_err: Exception | None = None
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        try:
            return completion_fn(**kwargs)
        except Exception as err:
            if is_exception_named(err, "AuthenticationError"):
                raise RuntimeError(f"{provider_name} authentication failed.") from err
            if is_exception_named(err, "NotFoundError"):
                raise RuntimeError(
                    _litellm_not_found_message(provider_label=provider_name, model=model)
                ) from err
            if is_exception_named(err, "PermissionDeniedError"):
                raise RuntimeError(f"{provider_name} request forbidden: {err}") from err
            if is_exception_named(err, "BadRequestError"):
                maybe_raise_credit_exhausted(provider_name, err)
                message = getattr(err, "message", str(err))
                raise RuntimeError(
                    f"{provider_name} request rejected (HTTP 400): {message}"
                ) from err
            if (
                is_exception_named(err, "RateLimitError")
                or getattr(err, "status_code", None) == HTTPStatus.TOO_MANY_REQUESTS
            ):
                maybe_raise_credit_exhausted(provider_name, err)
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(
                        f"{provider_name} rate limit exceeded after "
                        f"{_RETRY_MAX_ATTEMPTS} attempts: {err}"
                    ) from err
                time.sleep(rate_limit_sleep_seconds(err, backoff))
                backoff *= 2
                continue
            last_err = err
            if attempt == _RETRY_MAX_ATTEMPTS - 1:
                raise RuntimeError(f"{provider_name} API failed: {err}") from err
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError(f"{provider_name} invocation failed") from last_err


def invoke_with_litellm_llm_retries(
    completion_fn: Callable[..., Any],
    kwargs: dict[str, Any],
    *,
    provider_label: str,
    api_key_env: str,
    model: str,
    on_model_fallback: Callable[[], dict[str, Any] | None],
) -> Any:
    from infrastructure.safety.guardrails.evaluator import GuardrailBlockedError

    backoff_seconds = _RETRY_INITIAL_BACKOFF_SEC
    last_err: Exception | None = None
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        try:
            return completion_fn(**kwargs)
        except GuardrailBlockedError:
            raise
        except Exception as err:
            if is_exception_named(err, "AuthenticationError"):
                raise RuntimeError(
                    f"{provider_label} authentication failed. Check {api_key_env} in your environment, .env, or secure local keychain."
                ) from err
            if is_exception_named(err, "NotFoundError"):
                rebuilt = on_model_fallback()
                if rebuilt is not None:
                    kwargs = rebuilt
                    continue
                raise RuntimeError(
                    _litellm_not_found_message(provider_label=provider_label, model=model)
                ) from err
            if is_exception_named(err, "BadRequestError"):
                message = str(getattr(err, "message", err))
                if "model identifier" in message.lower():
                    rebuilt = on_model_fallback()
                    if rebuilt is not None:
                        kwargs = rebuilt
                        continue
                raise RuntimeError(
                    f"{provider_label} request rejected (HTTP 400): {message}"
                ) from err
            if (
                is_exception_named(err, "RateLimitError")
                or getattr(err, "status_code", None) == HTTPStatus.TOO_MANY_REQUESTS
            ):
                last_err = err
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(
                        f"{provider_label} rate limit exceeded (HTTP 429) after multiple retries. "
                        "Check your quota and billing details."
                    ) from err
                suggested = extract_retry_after_seconds(err) or 0.0
                wait = max(suggested, backoff_seconds)
                time.sleep(wait)
                backoff_seconds = wait * 2
                continue
            last_err = err
            if attempt == _RETRY_MAX_ATTEMPTS - 1:
                raise RuntimeError(
                    "LLM API request failed after multiple retries. Try again in a few seconds."
                ) from err
            time.sleep(backoff_seconds)
            backoff_seconds *= 2
    raise RuntimeError("LLM invocation failed without a concrete error") from last_err


def stream_with_litellm_retries(
    completion_fn: Callable[..., Any],
    kwargs: dict[str, Any],
    *,
    provider_label: str,
    api_key_env: str,
    model: str,
    on_model_fallback: Callable[[], dict[str, Any] | None],
) -> Iterator[str]:
    from infrastructure.safety.guardrails.evaluator import GuardrailBlockedError

    backoff_seconds = _RETRY_INITIAL_BACKOFF_SEC
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        emitted = False
        try:
            stream = completion_fn(stream=True, **kwargs)
            for chunk in stream:
                content = stream_chunk_delta_content(chunk)
                if content:
                    emitted = True
                    yield content
            return
        except GuardrailBlockedError:
            raise
        except Exception as err:
            if emitted:
                raise
            if is_exception_named(err, "AuthenticationError"):
                raise RuntimeError(
                    f"{provider_label} authentication failed. Check {api_key_env} in your environment, .env, or secure local keychain."
                ) from err
            if is_exception_named(err, "NotFoundError"):
                rebuilt = on_model_fallback()
                if rebuilt is not None:
                    kwargs = rebuilt
                    continue
                raise RuntimeError(
                    _litellm_not_found_message(provider_label=provider_label, model=model)
                ) from err
            if is_exception_named(err, "BadRequestError"):
                message = str(getattr(err, "message", err))
                if "model identifier" in message.lower():
                    rebuilt = on_model_fallback()
                    if rebuilt is not None:
                        kwargs = rebuilt
                        continue
                raise RuntimeError(
                    f"{provider_label} request rejected (HTTP 400): {message}"
                ) from err
            if (
                is_exception_named(err, "RateLimitError")
                or getattr(err, "status_code", None) == HTTPStatus.TOO_MANY_REQUESTS
            ):
                if attempt == _RETRY_MAX_ATTEMPTS - 1:
                    raise RuntimeError(
                        f"{provider_label} rate limit exceeded (HTTP 429) after multiple retries. "
                        "Check your quota and billing details."
                    ) from err
                suggested = extract_retry_after_seconds(err) or 0.0
                wait = max(suggested, backoff_seconds)
                time.sleep(wait)
                backoff_seconds = wait * 2
                continue
            if attempt == _RETRY_MAX_ATTEMPTS - 1:
                raise RuntimeError(
                    "LLM API request failed after multiple retries. Try again in a few seconds."
                ) from err
            time.sleep(backoff_seconds)
            backoff_seconds *= 2
