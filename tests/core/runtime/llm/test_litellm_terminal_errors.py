from __future__ import annotations

from functools import partial
from typing import Any

import httpx
import litellm
import openai
import pytest

from core.llm.shared.llm_retry import LLMCreditExhaustedError
from core.llm.transports.litellm import clients
from core.llm.transports.litellm.clients import LiteLLMAgentClient, LiteLLMLLMClient


@pytest.mark.parametrize("mode", ["agent", "invoke", "stream"])
@pytest.mark.parametrize(
    "kind",
    [
        "ContextWindowExceededError",
        "ContentPolicyViolationError",
        "Timeout",
        "PermissionDeniedError",
        "UnprocessableEntityError",
        "quota",
    ],
)
def test_terminal_errors_do_not_replay_the_request(
    monkeypatch: pytest.MonkeyPatch, mode: str, kind: str
) -> None:
    attempts: list[dict[str, Any]] = []
    sleeps: list[float] = []
    monkeypatch.setattr("core.llm.shared.openai_chat_completions.time.sleep", sleeps.append)

    def completion(**kwargs: Any) -> Any:
        attempts.append(kwargs)
        error_type = litellm.RateLimitError if kind == "quota" else getattr(litellm, kind)
        response_args = {}
        if kind in {"PermissionDeniedError", "UnprocessableEntityError"}:
            from http import HTTPStatus

            status = (
                HTTPStatus.FORBIDDEN
                if kind == "PermissionDeniedError"
                else HTTPStatus.UNPROCESSABLE_ENTITY
            )
            response_args["response"] = httpx.Response(
                status, request=httpx.Request("POST", "https://example.test")
            )
        raise error_type(
            message="insufficient_quota" if kind == "quota" else "offline rejection",
            model="test",
            llm_provider="openai",
            **response_args,
        )

    kwargs: dict[str, Any] = {"litellm_model": "openai/test", "completion_func": completion}
    expected = LLMCreditExhaustedError if kind == "quota" else RuntimeError
    with pytest.raises(expected):
        if mode == "agent":
            LiteLLMAgentClient(**kwargs).invoke([{"role": "user", "content": "hello"}])
        elif mode == "stream":
            list(LiteLLMLLMClient(**kwargs).invoke_stream("hello"))
        else:
            LiteLLMLLMClient(**kwargs).invoke("hello")
    assert len(attempts) == 1
    assert sleeps == []


@pytest.mark.parametrize("mode", ["agent", "invoke", "stream"])
def test_transient_rate_limits_still_retry_and_disable_nested_sdk_retries(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    attempts: list[dict[str, Any]] = []
    monkeypatch.setattr("core.llm.shared.openai_chat_completions.time.sleep", lambda _delay: None)

    def completion(**kwargs: Any) -> Any:
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise litellm.RateLimitError(
                message="rate limit exceeded", model="test", llm_provider="openai"
            )
        response = {"choices": [{"message": {"content": "ok"}, "delta": {"content": "ok"}}]}
        return iter([response]) if kwargs.get("stream") else response

    kwargs: dict[str, Any] = {"litellm_model": "openai/test", "completion_func": completion}
    if mode == "agent":
        result = LiteLLMAgentClient(**kwargs).invoke([{"role": "user", "content": "hello"}]).content
    elif mode == "stream":
        result = "".join(LiteLLMLLMClient(**kwargs).invoke_stream("hello"))
    else:
        result = LiteLLMLLMClient(**kwargs).invoke("hello").content
    assert result == "ok"
    assert len(attempts) == 2
    assert all(request["max_retries"] == 0 and request["num_retries"] == 0 for request in attempts)


@pytest.mark.parametrize("mode", ["agent", "invoke", "stream"])
def test_transport_timeout_is_sent_once_even_with_sdk_default_retries(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("offline timeout", request=request)

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        sdk = openai.OpenAI(api_key="test-key", http_client=http)
        monkeypatch.setattr(clients, "completion", partial(litellm.completion, client=sdk))
        with pytest.raises(RuntimeError, match="timed out; it was not retried"):
            if mode == "agent":
                LiteLLMAgentClient(litellm_model="openai/audit-model").invoke(
                    [{"role": "user", "content": "hi"}]
                )
            elif mode == "stream":
                list(LiteLLMLLMClient(litellm_model="openai/audit-model").invoke_stream("hi"))
            else:
                LiteLLMLLMClient(litellm_model="openai/audit-model").invoke("hi")
    assert len(requests) == 1
