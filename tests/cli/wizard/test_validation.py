from __future__ import annotations

import httpx
import pytest
from anthropic import AuthenticationError as AnthropicAuthError
from openai import AuthenticationError as OpenAIAuthError

from app.cli.wizard.config import PROVIDER_BY_VALUE
from app.cli.wizard.validation import validate_provider_credentials


@pytest.fixture(autouse=True)
def _preload_sdk_error_classes(monkeypatch) -> None:
    """Pre-populate the module-level ``*AuthError`` globals in validation.py.

    ``_load_anthropic_client``/``_load_openai_client`` lazily import the SDKs
    and override the module-level ``Anthropic``/``OpenAI`` names when their
    matching ``*AuthError`` is ``None``. If a test monkeypatches only the
    client class (``Anthropic`` / ``OpenAI``), the first call to the loader
    re-imports and silently replaces that monkeypatch with the real SDK —
    which then hits the real network using any ``ANTHROPIC_API_KEY`` /
    ``OPENAI_API_KEY`` leaked in from the test environment.

    By setting the ``*AuthError`` globals here to the real classes (already
    imported at the top of this file), we short-circuit the loader's import
    branch and make monkeypatches of ``Anthropic`` / ``OpenAI`` reliable.
    """
    monkeypatch.setattr("app.cli.wizard.validation.AnthropicAuthError", AnthropicAuthError)
    monkeypatch.setattr("app.cli.wizard.validation.OpenAIAuthError", OpenAIAuthError)


class _FakeAnthropicTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeAnthropicResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeAnthropicTextBlock(text)]


class _FakeAnthropicMessages:
    def __init__(self, result: object) -> None:
        self._result = result

    def create(self, **_kwargs):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeAnthropicClient:
    def __init__(self, result: object) -> None:
        self.messages = _FakeAnthropicMessages(result)


class _FakeOpenAIMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeOpenAIChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeOpenAIMessage(content)


class _FakeOpenAIResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeOpenAIChoice(content)]


class _FakeOpenAICompletions:
    def __init__(self, result: object) -> None:
        self._result = result

    def create(self, **_kwargs):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeOpenAIChat:
    def __init__(self, result: object) -> None:
        self.completions = _FakeOpenAICompletions(result)


class _FakeOpenAIClient:
    def __init__(self, result: object) -> None:
        self.chat = _FakeOpenAIChat(result)


def _request(url: str) -> httpx.Request:
    return httpx.Request("POST", url)


def test_validate_provider_credentials_returns_failure_for_bad_anthropic_key(monkeypatch) -> None:
    auth_error = AnthropicAuthError(
        "unauthorized",
        response=httpx.Response(401, request=_request("https://api.anthropic.com/v1/messages")),
        body=None,
    )
    monkeypatch.setattr(
        "app.cli.wizard.validation.Anthropic",
        lambda **_kwargs: _FakeAnthropicClient(auth_error),
    )

    result = validate_provider_credentials(
        provider=PROVIDER_BY_VALUE["anthropic"],
        api_key="bad-key",
        model="claude-opus-4-5",
    )

    assert result.ok is False
    assert result.detail == "Anthropic rejected the API key."


def test_validate_provider_credentials_returns_success_for_valid_anthropic_key(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.wizard.validation.Anthropic",
        lambda **_kwargs: _FakeAnthropicClient(_FakeAnthropicResponse("OpenSRE ready")),
    )

    result = validate_provider_credentials(
        provider=PROVIDER_BY_VALUE["anthropic"],
        api_key="good-key",
        model="claude-opus-4-5",
    )

    assert result.ok is True
    assert result.detail == "Anthropic API key validated."
    assert result.sample_response == "OpenSRE ready"


def test_validate_provider_credentials_returns_failure_for_bad_openai_key(monkeypatch) -> None:
    auth_error = OpenAIAuthError(
        "unauthorized",
        response=httpx.Response(401, request=_request("https://api.openai.com/v1/chat/completions")),
        body=None,
    )
    monkeypatch.setattr(
        "app.cli.wizard.validation.OpenAI",
        lambda **_kwargs: _FakeOpenAIClient(auth_error),
    )

    result = validate_provider_credentials(
        provider=PROVIDER_BY_VALUE["openai"],
        api_key="bad-key",
        model="gpt-5-mini",
    )

    assert result.ok is False
    assert result.detail == "OpenAI rejected the API key."


def test_validate_provider_credentials_returns_success_for_valid_openai_key(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.wizard.validation.OpenAI",
        lambda **_kwargs: _FakeOpenAIClient(_FakeOpenAIResponse("OpenSRE ready")),
    )

    result = validate_provider_credentials(
        provider=PROVIDER_BY_VALUE["openai"],
        api_key="good-key",
        model="gpt-5-mini",
    )

    assert result.ok is True
    assert result.detail == "OpenAI API key validated."
    assert result.sample_response == "OpenSRE ready"
