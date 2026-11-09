from __future__ import annotations

import httpx
import pytest
from anthropic import AuthenticationError as AnthropicAuthError
from openai import AuthenticationError as OpenAIAuthError

from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE, SUPPORTED_PROVIDERS
from surfaces.shared.llm_setup.validation import (
    _get_provider_base_url,
    validate_provider_credentials,
)


@pytest.fixture(autouse=True)
def _preload_sdk_error_classes(monkeypatch) -> None:
    """Pre-populate the module-level ``*AuthError`` globals in validation.py.

    ``_load_anthropic_client``/``load_openai_client`` lazily import the SDKs
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
    monkeypatch.setattr(
        "surfaces.shared.llm_setup.validation.AnthropicAuthError", AnthropicAuthError
    )
    monkeypatch.setattr("surfaces.shared.llm_setup.openai_client.OpenAIAuthError", OpenAIAuthError)


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
        "surfaces.shared.llm_setup.validation.Anthropic",
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
        "surfaces.shared.llm_setup.validation.Anthropic",
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
        response=httpx.Response(
            401, request=_request("https://api.openai.com/v1/chat/completions")
        ),
        body=None,
    )
    monkeypatch.setattr(
        "surfaces.shared.llm_setup.openai_client.OpenAI",
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
        "surfaces.shared.llm_setup.openai_client.OpenAI",
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


def test_validate_provider_credentials_azure_not_found_lists_deployments(monkeypatch) -> None:
    monkeypatch.setattr(
        "surfaces.shared.llm_setup.openai_client.OpenAI",
        lambda **_kwargs: _FakeOpenAIClient(RuntimeError("Error code: 404 - DeploymentNotFound")),
    )
    monkeypatch.setattr(
        "surfaces.shared.llm_setup.azure_validation.list_azure_openai_deployments",
        lambda **_kwargs: ["gpt-4.1-mini"],
    )
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.openai.azure.com")

    result = validate_provider_credentials(
        provider=PROVIDER_BY_VALUE["azure-openai"],
        api_key="good-key",
        model="gpt-4.1",
    )

    assert result.ok is False
    assert "deployment 'gpt-4.1'" in result.detail
    assert "Available deployments: gpt-4.1-mini" in result.detail


def test_get_provider_base_url_deepseek() -> None:
    from config.llm_models import DEEPSEEK_BASE_URL

    assert _get_provider_base_url("deepseek") == DEEPSEEK_BASE_URL


def test_get_provider_base_url_groq() -> None:
    from config.llm_models import GROQ_BASE_URL

    assert _get_provider_base_url("groq") == GROQ_BASE_URL


def test_get_provider_base_url_openai_returns_none() -> None:
    """Native OpenAI should return None (uses default base_url)."""
    assert _get_provider_base_url("openai") is None


# --- AC-4 (#3591): MiniMax must not be probed against api.openai.com --------


def test_get_provider_base_url_minimax() -> None:
    """A valid MiniMax key is currently reported as rejected.

    `minimax` is a live `credential_kind="api_key"` provider that reaches the
    wizard's key prompt, but `_get_provider_base_url` has no branch for it and
    returns None — so the OpenAI SDK falls back to its default host and the key
    is sent to api.openai.com, which of course rejects it. `MINIMAX_BASE_URL`
    has existed in config/llm_models.py all along; it was simply never wired in.

    Latent today (it already breaks `opensre auth`); a HARD BLOCK the moment
    onboarding gates on validation, because MiniMax users could then never
    finish the wizard at all.
    """
    from config.llm_models import MINIMAX_BASE_URL

    assert _get_provider_base_url("minimax") == MINIMAX_BASE_URL
    assert "openai.com" not in MINIMAX_BASE_URL


@pytest.mark.parametrize(
    "provider_value",
    sorted(
        provider.value
        for provider in SUPPORTED_PROVIDERS
        if provider.credential_kind == "api_key"
        # Native OpenAI uses the SDK default; Anthropic, Azure, and custom-anthropic
        # never reach `_get_provider_base_url` (they have their own dispatch branches).
        # custom-openai's base URL comes from the user's env, not a static constant,
        # so it is covered by a dedicated env-driven test instead.
        and provider.value
        not in {"openai", "anthropic", "azure-openai", "custom-openai", "custom-anthropic"}
    ),
)
def test_every_openai_compatible_provider_has_a_base_url(provider_value: str) -> None:
    """Coverage gate: catches the NEXT provider added without a base URL.

    Iterates the live provider list rather than a hardcoded one, so a bare
    `assert minimax == URL` cannot be mistaken for protection.
    """
    base_url = _get_provider_base_url(provider_value)
    assert base_url is not None, (
        f"{provider_value!r} is an OpenAI-compatible api_key provider with no base_url: "
        "its credentials would be validated against api.openai.com and always rejected"
    )


def test_get_provider_base_url_custom_openai_uses_env(monkeypatch) -> None:
    # custom-openai's probe must hit the user's gateway, not api.openai.com.
    monkeypatch.setenv("CUSTOM_OPENAI_BASE_URL", "http://localhost:4000/v1/")
    assert _get_provider_base_url("custom-openai") == "http://localhost:4000/v1"


def test_get_provider_base_url_custom_openai_none_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("CUSTOM_OPENAI_BASE_URL", raising=False)
    assert _get_provider_base_url("custom-openai") is None


def test_validate_custom_anthropic_probes_the_override_endpoint(monkeypatch) -> None:
    # The custom-anthropic probe must construct the Anthropic client with base_url,
    # so a "validated" result reflects the endpoint investigate will use.
    monkeypatch.setenv("CUSTOM_ANTHROPIC_BASE_URL", "https://proxy.example.com")
    captured: dict[str, object] = {}

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.messages = self

        def create(self, **_kwargs):
            return type("R", (), {"content": []})()

    monkeypatch.setattr(
        "surfaces.shared.llm_setup.validation._load_anthropic_client",
        lambda: (_FakeAnthropic, Exception),
    )
    provider = PROVIDER_BY_VALUE["custom-anthropic"]
    result = validate_provider_credentials(provider=provider, api_key="k", model="claude-x")
    assert result.ok is True
    assert captured.get("base_url") == "https://proxy.example.com"
