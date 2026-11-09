from __future__ import annotations

from collections.abc import Iterator
from http import HTTPStatus
from typing import Any

import litellm
import pytest

from core.llm.shared.openai_token_limits import litellm_token_retry_kwargs
from core.llm.transports.litellm.clients import LiteLLMAgentClient, LiteLLMLLMClient


def _rejection(
    *, body: dict[str, Any] | None = None, message: str | None = None
) -> litellm.BadRequestError:
    return litellm.BadRequestError(
        message=message or "Unsupported parameter: 'max_tokens' is not supported with this model.",
        model="prod-chat",
        llm_provider="azure",
        body=body,
    )


@pytest.mark.parametrize(
    "body,message,expected",
    [
        ({"param": "max_tokens", "code": "unsupported_parameter"}, "Rejected", True),
        ({"error": {"param": "max_tokens", "code": "unsupported_parameter"}}, "Rejected", True),
        (None, 'Unsupported parameter: "max_tokens" is not supported with this model.', True),
        ({"param": "max_tokens", "code": "invalid_value"}, "max_tokens must be positive", False),
        ({"param": "messages", "code": "content_filter"}, "Content filtered", False),
    ],
)
def test_token_retry_uses_explicit_rejection_and_leaves_request_unchanged(
    body: dict[str, Any] | None, message: str, expected: bool
) -> None:
    request = {"model": "azure/prod-chat", "max_tokens": 37, "messages": []}
    error = _rejection(body=body, message=message)
    retry = litellm_token_retry_kwargs(request, error)
    assert request == {"model": "azure/prod-chat", "max_tokens": 37, "messages": []}
    if expected:
        assert retry == {"model": "azure/prod-chat", "max_completion_tokens": 37, "messages": []}
        assert litellm_token_retry_kwargs(retry, error) is None
    else:
        assert retry is None


def test_token_retry_ignores_other_routes_and_status_codes() -> None:
    error = _rejection()
    assert (
        litellm_token_retry_kwargs({"model": "openai/prod-chat", "max_tokens": 37}, error) is None
    )
    error.status_code = HTTPStatus.UNAUTHORIZED
    assert litellm_token_retry_kwargs({"model": "azure/prod-chat", "max_tokens": 37}, error) is None
    error.status_code = HTTPStatus.BAD_REQUEST
    assert (
        litellm_token_retry_kwargs(
            {"model": "azure/prod-chat", "max_tokens": 37, "max_completion_tokens": 37}, error
        )
        is None
    )


@pytest.mark.parametrize("mode", ["agent", "invoke", "stream"])
def test_token_correction_still_works_after_transient_retry_budget(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    requests: list[dict[str, Any]] = []
    sleeps: list[float] = []
    monkeypatch.setattr("core.llm.shared.openai_chat_completions.time.sleep", sleeps.append)

    def response() -> dict[str, Any]:
        if len(requests) < 3:
            raise litellm.RateLimitError("slow down", model="prod-chat", llm_provider="azure")
        if "max_tokens" in requests[-1]:
            raise _rejection()
        return {"choices": [{"message": {"content": "ok"}, "delta": {"content": "ok"}}]}

    def chunks() -> Iterator[dict[str, Any]]:
        yield response()

    def completion(**kwargs: Any) -> Any:
        requests.append(kwargs)
        return chunks() if kwargs.get("stream") else response()

    kwargs: dict[str, Any] = {"litellm_model": "azure/prod-chat", "completion_func": completion}
    if mode == "agent":
        assert (
            LiteLLMAgentClient(**kwargs).invoke([{"role": "user", "content": "hi"}]).content == "ok"
        )
    elif mode == "invoke":
        assert LiteLLMLLMClient(**kwargs).invoke("hi").content == "ok"
    else:
        assert list(LiteLLMLLMClient(**kwargs).invoke_stream("hi")) == ["ok"]
    assert len(requests) == 4
    assert len(sleeps) == 2
    assert "max_tokens" in requests[2]
    assert "max_completion_tokens" in requests[3]


def test_stream_does_not_restart_after_emitting_text() -> None:
    requests: list[dict[str, Any]] = []

    def chunks() -> Iterator[dict[str, Any]]:
        yield {"choices": [{"delta": {"content": "partial"}}]}
        raise _rejection()

    def completion(**kwargs: Any) -> Iterator[dict[str, Any]]:
        requests.append(kwargs)
        return chunks()

    client = LiteLLMLLMClient(litellm_model="azure/prod-chat", completion_func=completion)
    stream = client.invoke_stream("hi")
    assert next(stream) == "partial"
    with pytest.raises(litellm.BadRequestError):
        next(stream)
    assert len(requests) == 1


def test_second_call_on_cached_client_negotiates_its_own_token_parameter() -> None:
    requests: list[dict[str, Any]] = []
    nested = False

    def completion(**kwargs: Any) -> dict[str, Any]:
        nonlocal nested
        requests.append(kwargs)
        if "max_tokens" in kwargs:
            if not nested:
                nested = True
                assert client.invoke("another turn").content == "ok"
            raise _rejection()
        return {"choices": [{"message": {"content": "ok"}}]}

    client = LiteLLMLLMClient(litellm_model="azure/prod-chat", completion_func=completion)
    assert client.invoke("first turn").content == "ok"
    assert ["max_tokens" in request for request in requests] == [True, True, False, False]
