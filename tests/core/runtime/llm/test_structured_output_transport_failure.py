"""A timed-out or dropped native structured call is neither retried nor re-sent as a fallback.

The request may already have run upstream and been billed. Schema or format rejections
still fall back to the prompt-schema call once.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import anthropic
import httpx
import openai
import pytest
from pydantic import BaseModel

from config.constants.llm import OPENSRE_LLM_NATIVE_STRUCTURED_OUTPUT_ENV
from core.llm.shared.openai_chat_completions import _RETRY_MAX_ATTEMPTS
from core.llm.shared.structured_output import StructuredOutputClient
from core.llm.shared.transport_failure import is_transport_failure
from core.llm.transports.sdk.llm_clients import LLMClient, OpenAILLMClient

_REQUEST = httpx.Request("POST", "https://llm.invalid/v1/chat/completions")


class _Payload(BaseModel):
    verdict: str


@pytest.fixture(autouse=True)
def _native_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_LLM_NATIVE_STRUCTURED_OUTPUT_ENV, "1")
    monkeypatch.setattr("core.llm.transports.sdk.llm_clients.time.sleep", lambda _s: None)
    monkeypatch.setattr("core.llm.hosted_credits.admit_hosted_credits", lambda: None)


def _openai_client(monkeypatch: pytest.MonkeyPatch) -> tuple[OpenAILLMClient, MagicMock]:
    client = OpenAILLMClient.__new__(OpenAILLMClient)
    client._model = "gpt-5.4-mini"
    client._max_tokens = 256
    client._temperature = None
    client._api_key_env = "OPENAI_API_KEY"
    client._api_key_default = "test-key"
    client._reasoning_effort = None
    client._provider_label = "OpenAI"
    sdk = MagicMock()
    client._client = sdk
    monkeypatch.setattr(client, "_ensure_client", lambda: sdk)
    return client, sdk


def _anthropic_client(monkeypatch: pytest.MonkeyPatch) -> tuple[LLMClient, MagicMock]:
    client = LLMClient.__new__(LLMClient)
    client._model = "claude-sonnet-4-6"
    client._max_tokens = 256
    client._temperature = None
    client._api_key = "test-key"
    sdk = MagicMock()
    client._client = sdk
    monkeypatch.setattr(client, "_ensure_client", lambda: None)
    return client, sdk


def _fallback_completion() -> SimpleNamespace:
    message = SimpleNamespace(content='{"verdict": "fallback"}')
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


_OPENAI_TRANSPORT_ERRORS = [
    pytest.param(openai.APITimeoutError(request=_REQUEST), id="timeout"),
    pytest.param(openai.APIConnectionError(request=_REQUEST), id="connection"),
]

_ANTHROPIC_TRANSPORT_ERRORS = [
    pytest.param(anthropic.APITimeoutError(request=_REQUEST), id="timeout"),
    pytest.param(anthropic.APIConnectionError(request=_REQUEST), id="connection"),
]


@pytest.mark.parametrize("error", _OPENAI_TRANSPORT_ERRORS)
def test_openai_transport_failure_makes_one_request(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    client, sdk = _openai_client(monkeypatch)
    sdk.chat.completions.parse.side_effect = error
    sdk.chat.completions.create.return_value = _fallback_completion()

    with pytest.raises(RuntimeError) as raised:
        StructuredOutputClient(client, _Payload).invoke("diagnose this")

    assert raised.value.__cause__ is error
    assert sdk.chat.completions.parse.call_count == 1
    assert sdk.chat.completions.create.call_count == 0


def test_openai_schema_rejection_still_falls_back_once(monkeypatch: pytest.MonkeyPatch) -> None:
    client, sdk = _openai_client(monkeypatch)
    rejection = openai.BadRequestError(
        "response_format json_schema is not supported",
        response=httpx.Response(400, request=_REQUEST),
        body=None,
    )
    sdk.chat.completions.parse.side_effect = rejection
    sdk.chat.completions.create.return_value = _fallback_completion()

    parsed = StructuredOutputClient(client, _Payload).invoke("diagnose this")

    assert parsed.verdict == "fallback"
    assert sdk.chat.completions.parse.call_count == 1
    assert sdk.chat.completions.create.call_count == 1


@pytest.mark.parametrize("error", _ANTHROPIC_TRANSPORT_ERRORS)
def test_anthropic_transport_failure_is_not_followed_by_fallback(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    client, sdk = _anthropic_client(monkeypatch)
    sdk.messages.create.side_effect = error

    with pytest.raises(RuntimeError) as raised:
        StructuredOutputClient(client, _Payload).invoke("diagnose this")

    assert raised.value.__cause__ is error
    assert sdk.messages.create.call_count == _RETRY_MAX_ATTEMPTS


def _wrapped(inner: BaseException, depth: int) -> BaseException:
    err = inner
    for _ in range(depth):
        outer = RuntimeError("wrapped")
        outer.__cause__ = err
        err = outer
    return err


def _raised_in_handler(inner: BaseException) -> BaseException:
    outer = RuntimeError("wrapped without from")
    outer.__context__ = inner
    return outer


def _cycle() -> BaseException:
    first, second = RuntimeError("first"), RuntimeError("second")
    first.__cause__ = second
    second.__cause__ = first
    return first


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        pytest.param(openai.APITimeoutError(request=_REQUEST), True, id="openai-timeout"),
        pytest.param(openai.APIConnectionError(request=_REQUEST), True, id="openai-connection"),
        pytest.param(anthropic.APITimeoutError(request=_REQUEST), True, id="anthropic-timeout"),
        pytest.param(
            _wrapped(anthropic.APIConnectionError(request=_REQUEST), 3), True, id="wrapped-deep"
        ),
        pytest.param(
            _raised_in_handler(openai.APITimeoutError(request=_REQUEST)), True, id="context-only"
        ),
        pytest.param(RuntimeError("provider rejected output_config"), False, id="plain"),
        pytest.param(_wrapped(ValueError("bad schema"), 2), False, id="wrapped-unrelated"),
        pytest.param(_cycle(), False, id="cause-cycle"),
    ],
)
def test_is_transport_failure_follows_the_cause_chain(error: BaseException, expected: Any) -> None:
    assert is_transport_failure(error) is expected
