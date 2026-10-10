from __future__ import annotations

import pytest
from anthropic import AuthenticationError, NotFoundError, PermissionDeniedError
from anthropic import BadRequestError as AnthropicBadRequestError
from openai import APITimeoutError as OpenAITimeoutError
from openai import BadRequestError as OpenAIBadRequestError
from openai import RateLimitError as OpenAIRateLimitError

import core.llm.transports.sdk.llm_clients as sdk_llm
from core.llm import factory
from core.llm.factory import reset_llm_clients
from core.llm.shared import usage as usage_mod
from core.llm.shared.openai_chat_completions import _RETRY_MAX_ATTEMPTS
from core.llm.shared.structured_output import extract_json_payload


class _FakeAnthropicMessages:
    def create(self, **_kwargs):
        raise AssertionError("unexpected network call in unit test")


class _FakeAnthropic:
    last_api_key: str | None = None

    def __init__(self, *, api_key: str, timeout: float) -> None:
        _FakeAnthropic.last_api_key = api_key
        self.timeout = timeout
        self.messages = _FakeAnthropicMessages()


class _FakeOpenAICompletions:
    def create(self, **_kwargs):
        raise AssertionError("unexpected network call in unit test")


class _FakeOpenAIChat:
    def __init__(self) -> None:
        self.completions = _FakeOpenAICompletions()


class _FakeOpenAI:
    last_api_key: str | None = None
    last_base_url: str | None = None
    last_default_headers: dict[str, str] | None = None
    init_api_keys: list[str] = []

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        timeout: float,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        _FakeOpenAI.last_api_key = api_key
        _FakeOpenAI.last_base_url = base_url
        _FakeOpenAI.last_default_headers = default_headers
        _FakeOpenAI.init_api_keys.append(api_key)
        self.base_url = base_url
        self.timeout = timeout
        self.default_headers = default_headers
        self.chat = _FakeOpenAIChat()


@pytest.fixture(autouse=True)
def _reset_fake_openai_state() -> None:
    _FakeOpenAI.last_api_key = None
    _FakeOpenAI.last_base_url = None
    _FakeOpenAI.last_default_headers = None
    _FakeOpenAI.init_api_keys = []


def test_openai_llm_client_defers_openai_until_ensure(monkeypatch) -> None:
    """Avoid constructing OpenAI in __init__: sdk 2.34+ rejects empty api_key."""
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env_var: ""
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _FakeOpenAI)

    sdk_llm.OpenAILLMClient(model="gpt-4.1-mini")

    assert _FakeOpenAI.last_api_key is None
    assert _FakeOpenAI.init_api_keys == []


def test_openai_llm_client_reads_secure_local_api_key(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key",
        lambda env_var: "stored-openai-key" if env_var == "OPENAI_API_KEY" else "",
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _FakeOpenAI)

    client = sdk_llm.OpenAILLMClient(model="gpt-5.4")
    client._ensure_client()

    assert _FakeOpenAI.last_api_key == "stored-openai-key"
    assert _FakeOpenAI.init_api_keys == ["stored-openai-key"]


def test_openai_llm_client_adds_reasoning_effort_for_reasoning_models(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key",
        lambda env_var: "stored-openai-key" if env_var == "OPENAI_API_KEY" else "",
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _FakeOpenAI)
    monkeypatch.setenv("OPENSRE_REASONING_EFFORT", "xhigh")

    client = sdk_llm.OpenAILLMClient(model="gpt-5.2")
    kwargs = client._build_request_kwargs("hello")

    assert kwargs["reasoning_effort"] == "xhigh"


def test_openai_llm_client_omits_reasoning_effort_for_non_reasoning_models(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key",
        lambda env_var: "stored-openai-key" if env_var == "OPENAI_API_KEY" else "",
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _FakeOpenAI)
    monkeypatch.setenv("OPENSRE_REASONING_EFFORT", "high")

    client = sdk_llm.OpenAILLMClient(model="gpt-4.1-mini")
    kwargs = client._build_request_kwargs("hello")

    assert "reasoning_effort" not in kwargs


def test_openai_llm_client_defaults_only_gpt_6_1_to_extra_high(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key",
        lambda env_var: "stored-openai-key" if env_var == "OPENAI_API_KEY" else "",
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _FakeOpenAI)
    monkeypatch.delenv("OPENSRE_REASONING_EFFORT", raising=False)

    gpt_6_1 = sdk_llm.OpenAILLMClient(model="gpt-6.1-sol")._build_request_kwargs("hello")
    gpt_5_6 = sdk_llm.OpenAILLMClient(model="gpt-5.6-sol")._build_request_kwargs("hello")

    assert gpt_6_1["reasoning_effort"] == "xhigh"
    assert "reasoning_effort" not in gpt_5_6


def test_openai_llm_client_invoke_fails_when_key_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env_var: ""
    )
    client = sdk_llm.OpenAILLMClient(model="gpt-4.1-mini")

    with pytest.raises(RuntimeError, match="Missing OPENAI_API_KEY"):
        client.invoke("hello")


def test_openai_llm_client_rebuilds_client_when_key_rotates(monkeypatch) -> None:
    state = {"key": "first-key"}
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env_var: state["key"]
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _FakeOpenAI)
    client = sdk_llm.OpenAILLMClient(model="gpt-4.1-mini")

    client._ensure_client()
    state["key"] = "second-key"
    client._ensure_client()

    assert _FakeOpenAI.init_api_keys == ["first-key", "second-key"]


class _InactiveGuardrailEvaluator:
    is_active = False

    def apply(self, content: str) -> str:
        return content


class _RecordingBedrockRuntime:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.converse_calls: list[dict] = []

    def converse(self, **kwargs) -> dict:
        self.converse_calls.append(kwargs)
        return self.response


def _make_fake_anthropic_bad_request_error(message: str = "invalid request") -> Exception:
    """Return a minimal Anthropic BadRequestError without constructing an HTTP response."""
    err = AnthropicBadRequestError.__new__(AnthropicBadRequestError)
    Exception.__init__(err, message)
    err.status_code = 400  # type: ignore[attr-defined]
    err.message = message  # type: ignore[attr-defined]
    err.body = {}  # type: ignore[attr-defined]
    err.request = None  # type: ignore[attr-defined]
    err.response = None  # type: ignore[attr-defined]
    return err


def _make_fake_openai_bad_request_error(message: str = "invalid request") -> Exception:
    """Return a minimal OpenAI BadRequestError without constructing an HTTP response."""
    err = OpenAIBadRequestError.__new__(OpenAIBadRequestError)
    Exception.__init__(err, message)
    err.status_code = 400  # type: ignore[attr-defined]
    err.message = message  # type: ignore[attr-defined]
    err.body = {}  # type: ignore[attr-defined]
    err.request = None  # type: ignore[attr-defined]
    err.response = None  # type: ignore[attr-defined]
    return err


def test_is_anthropic_bedrock_model_claude_ids() -> None:
    assert sdk_llm.is_anthropic_bedrock_model("anthropic.claude-3-haiku-20240307-v1:0")
    assert sdk_llm.is_anthropic_bedrock_model(
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    )


def test_is_anthropic_bedrock_model_foundation_model_arn() -> None:
    arn = "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-sonnet-20240229-v1:0"
    assert sdk_llm.is_anthropic_bedrock_model(arn)


def test_is_anthropic_bedrock_model_non_anthropic() -> None:
    assert not sdk_llm.is_anthropic_bedrock_model(
        "mistral.mistral-large-2402-v1:0",
    )


def test_is_anthropic_bedrock_model_application_inference_profile_arn() -> None:
    profile_arn = (
        "arn:aws:bedrock:us-east-2:012345678901:application-inference-profile/a1b2c3profile"
    )
    assert not sdk_llm.is_anthropic_bedrock_model(profile_arn)


def test_bedrock_client_routes_mistral_to_converse(monkeypatch) -> None:
    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    runtime = _RecordingBedrockRuntime(
        {"output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}}},
    )
    monkeypatch.setattr(sdk_llm.boto3, "client", lambda *_a, **_k: runtime)

    client = sdk_llm.BedrockLLMClient(model="mistral.mistral-large-2402-v1:0")
    assert client._use_anthropic is False
    resp = client.invoke([{"role": "user", "content": "hi"}])
    assert resp.content == "ok"
    assert len(runtime.converse_calls) == 1
    call = runtime.converse_calls[0]
    assert call["modelId"] == "mistral.mistral-large-2402-v1:0"
    assert call["messages"] == [
        {"role": "user", "content": [{"text": "hi"}]},
    ]
    assert "system" not in call


def test_invoke_converse_includes_optional_system_temperature(monkeypatch) -> None:
    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    runtime = _RecordingBedrockRuntime(
        {"output": {"message": {"role": "assistant", "content": [{"text": ""}, {"text": "x"}]}}},
    )
    monkeypatch.setattr(sdk_llm.boto3, "client", lambda *_a, **_k: runtime)

    client = sdk_llm.BedrockLLMClient(model="mistral.mini", temperature=0.4)
    client.invoke(
        [
            {"role": "system", "content": "context"},
            {"role": "user", "content": "q"},
        ],
    )

    kwargs = runtime.converse_calls[0]
    assert kwargs["system"] == [{"text": "context"}]
    assert kwargs["inferenceConfig"]["temperature"] == 0.4


def test_invoke_converse_raises_when_no_text_blocks(monkeypatch) -> None:
    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    runtime = _RecordingBedrockRuntime(
        {
            "stopReason": "tool_use",
            "output": {"message": {"role": "assistant", "content": [{"toolUse": {"name": "x"}}]}},
        },
    )
    monkeypatch.setattr(sdk_llm.boto3, "client", lambda *_a, **_k: runtime)

    client = sdk_llm.BedrockLLMClient(model="mistral.mini")
    with pytest.raises(RuntimeError, match="no text content"):
        client.invoke("hello")


def test_bedrock_application_inference_profile_arn_uses_converse(monkeypatch) -> None:
    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    runtime = _RecordingBedrockRuntime(
        {"output": {"message": {"role": "assistant", "content": [{"text": "via-converse"}]}}},
    )
    monkeypatch.setattr(sdk_llm.boto3, "client", lambda *_a, **_k: runtime)

    arn = "arn:aws:bedrock:us-west-2:123:application-inference-profile/p2"
    client = sdk_llm.BedrockLLMClient(model=arn)

    assert client._use_anthropic is False
    assert client.invoke("hi").content == "via-converse"


def test_bedrock_anthropic_bad_request_does_not_retry(monkeypatch) -> None:
    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    attempts: list[int] = []
    sleeps: list[float] = []

    class _Messages:
        def create(self, **_kwargs):
            attempts.append(1)
            raise _make_fake_anthropic_bad_request_error("invalid bedrock request")

    class _AnthropicBedrock:
        def __init__(self, **_kwargs) -> None:
            self.messages = _Messages()

    monkeypatch.setattr(sdk_llm, "AnthropicBedrock", _AnthropicBedrock)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    client = sdk_llm.BedrockLLMClient(model="anthropic.claude-test")
    with pytest.raises(RuntimeError, match="Bedrock Anthropic request rejected"):
        client.invoke("hello")

    assert attempts == [1]
    assert sleeps == []


def test_bedrock_anthropic_stream_bad_request_does_not_retry(monkeypatch) -> None:
    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    attempts: list[int] = []
    sleeps: list[float] = []

    class _Messages:
        def create(self, **_kwargs):
            attempts.append(1)
            raise _make_fake_anthropic_bad_request_error("invalid bedrock stream request")

    class _AnthropicBedrock:
        def __init__(self, **_kwargs) -> None:
            self.messages = _Messages()

    monkeypatch.setattr(sdk_llm, "AnthropicBedrock", _AnthropicBedrock)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    client = sdk_llm.BedrockLLMClient(model="anthropic.claude-test")
    with pytest.raises(RuntimeError, match="Bedrock Anthropic request rejected"):
        list(client.invoke_stream("hello"))

    assert attempts == [1]
    assert sleeps == []


def test_anthropic_llm_client_reads_secure_local_api_key(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key",
        lambda env_var: "stored-anthropic-key" if env_var == "ANTHROPIC_API_KEY" else "",
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", _FakeAnthropic)

    client = sdk_llm.LLMClient(model="claude-opus-4")
    client._ensure_client()

    assert _FakeAnthropic.last_api_key == "stored-anthropic-key"


def test_minimax_llm_client_reads_api_key_and_base_url(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key",
        lambda env_var: "minimax-test-key" if env_var == "MINIMAX_API_KEY" else "",
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _FakeOpenAI)

    client = sdk_llm.OpenAILLMClient(
        model="MiniMax-M3",
        base_url="https://api.minimax.io/v1",
        api_key_env="MINIMAX_API_KEY",
        temperature=1.0,
    )
    client._ensure_client()

    assert _FakeOpenAI.last_api_key == "minimax-test-key"
    assert _FakeOpenAI.last_base_url == "https://api.minimax.io/v1"


def test_minimax_llm_client_temperature_is_set(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key",
        lambda env_var: "minimax-test-key" if env_var == "MINIMAX_API_KEY" else "",
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _FakeOpenAI)

    client = sdk_llm.OpenAILLMClient(
        model="MiniMax-M3",
        base_url="https://api.minimax.io/v1",
        api_key_env="MINIMAX_API_KEY",
        temperature=1.0,
    )
    assert client._temperature == 1.0


# ---------------------------------------------------------------------------
# LLMClient.invoke / invoke_stream — kwargs builder + streaming behavior
# ---------------------------------------------------------------------------


def _make_capturing_anthropic(
    *,
    response_text: str = "",
    chunks: list[str] | None = None,
):
    """Build a fake Anthropic class that captures kwargs and returns canned data.

    Distinct from the module-level ``_FakeAnthropic`` (which raises on any
    API call to guard ``_ensure_client`` tests). Closure-scoped so each test
    gets a fresh capture dict — no class-level state to reset between tests.
    """
    state: dict = {"kwargs": None}
    stream_chunks = list(chunks or [])

    class _Block:
        def __init__(self, text: str) -> None:
            self.type = "text"
            self.text = text

    class _Response:
        def __init__(self, text: str) -> None:
            self.content = [_Block(text)]

    class _StreamCM:
        def __init__(self) -> None:
            self.text_stream = iter(stream_chunks)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Messages:
        def create(self, **kwargs):
            state["kwargs"] = kwargs
            return _Response(response_text)

        def stream(self, **kwargs):
            state["kwargs"] = kwargs
            return _StreamCM()

    class _Anthropic:
        def __init__(self, *, api_key: str, timeout: float) -> None:
            self.api_key = api_key
            self.timeout = timeout
            self.messages = _Messages()

    return _Anthropic, state


def test_anthropic_invoke_forwards_built_kwargs_to_messages_create(monkeypatch) -> None:
    """Refactored invoke() still sends model, max_tokens, and messages to the SDK."""
    fake, captured = _make_capturing_anthropic(response_text="hello")
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", fake)

    client = sdk_llm.LLMClient(model="claude-test", max_tokens=64)
    response = client.invoke("hi")

    assert response.content == "hello"
    assert captured["kwargs"]["model"] == "claude-test"
    assert captured["kwargs"]["max_tokens"] == 64
    assert captured["kwargs"]["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_invoke_bad_request_does_not_retry(monkeypatch) -> None:
    attempts: list[int] = []
    sleeps: list[float] = []

    class _Messages:
        def create(self, **_kwargs):
            attempts.append(1)
            raise _make_fake_anthropic_bad_request_error("invalid anthropic request")

    class _Anthropic:
        def __init__(self, **_kwargs) -> None:
            self.messages = _Messages()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", _Anthropic)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    client = sdk_llm.LLMClient(model="claude-test")
    with pytest.raises(RuntimeError, match="Anthropic request rejected"):
        client.invoke("hello")

    assert attempts == [1]
    assert sleeps == []


def test_anthropic_invoke_stream_yields_text_stream_chunks(monkeypatch) -> None:
    """invoke_stream() routes through the same builder and yields SDK chunks in order."""
    fake, captured = _make_capturing_anthropic(chunks=["Hel", "lo, ", "world"])
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", fake)

    client = sdk_llm.LLMClient(model="claude-test", max_tokens=64)
    chunks = list(client.invoke_stream("hi"))

    assert chunks == ["Hel", "lo, ", "world"]
    assert captured["kwargs"]["model"] == "claude-test"
    assert captured["kwargs"]["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_invoke_stream_bad_request_does_not_retry(monkeypatch) -> None:
    attempts: list[int] = []
    sleeps: list[float] = []

    class _Messages:
        def stream(self, **_kwargs):
            attempts.append(1)
            raise _make_fake_anthropic_bad_request_error("invalid anthropic stream request")

    class _Anthropic:
        def __init__(self, **_kwargs) -> None:
            self.messages = _Messages()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", _Anthropic)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    client = sdk_llm.LLMClient(model="claude-test")
    with pytest.raises(RuntimeError, match="Anthropic request rejected"):
        list(client.invoke_stream("hello"))

    assert attempts == [1]
    assert sleeps == []


def test_anthropic_invoke_stream_applies_guardrails_to_input(monkeypatch) -> None:
    """The shared kwargs builder runs guardrail redaction before the stream opens."""
    fake, captured = _make_capturing_anthropic(chunks=["ok"])
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", fake)

    class _RedactingEngine:
        is_active = True

        def apply(self, content: str) -> str:
            return content.replace("secret", "[REDACTED]")

    import infrastructure.safety.guardrails.evaluator as engine_module

    monkeypatch.setattr(engine_module, "get_guardrail_evaluator", lambda: _RedactingEngine())

    client = sdk_llm.LLMClient(model="claude-test")
    list(client.invoke_stream("share my secret"))

    assert captured["kwargs"]["messages"][0]["content"] == "share my [REDACTED]"


def test_anthropic_invoke_stream_retries_when_no_chunk_emitted(monkeypatch) -> None:
    """Transient failure before any chunk yields → retry succeeds, caller sees recovered text."""
    attempts: list[bool] = []

    class _SuccessStream:
        def __init__(self) -> None:
            self.text_stream = iter(["recovered"])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Messages:
        def stream(self, **_kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise RuntimeError("Overloaded")
            return _SuccessStream()

    class _Anthropic:
        def __init__(self, **_kwargs) -> None:
            self.messages = _Messages()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", _Anthropic)
    # Skip the real backoff sleep so the test is fast.
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda _seconds: None)

    client = sdk_llm.LLMClient(model="claude-test")
    chunks = list(client.invoke_stream("hi"))

    assert chunks == ["recovered"]
    assert len(attempts) == 2  # First call raised; retry succeeded


def test_anthropic_invoke_stream_does_not_retry_after_yielding(monkeypatch) -> None:
    """Mid-stream failure must propagate; retrying would duplicate visible output."""
    attempts: list[bool] = []

    def _yield_then_raise():
        yield "partial"
        raise RuntimeError("connection dropped")

    class _Stream:
        def __init__(self) -> None:
            self.text_stream = _yield_then_raise()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Messages:
        def stream(self, **_kwargs):
            attempts.append(True)
            return _Stream()

    class _Anthropic:
        def __init__(self, **_kwargs) -> None:
            self.messages = _Messages()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", _Anthropic)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda _seconds: None)

    client = sdk_llm.LLMClient(model="claude-test")
    iterator = client.invoke_stream("hi")

    # First chunk reaches the caller — visible on the user's screen.
    assert next(iterator) == "partial"

    raised = False
    try:
        next(iterator)
    except RuntimeError as exc:
        raised = True
        assert "connection dropped" in str(exc)

    assert raised, "RuntimeError should have propagated"
    assert len(attempts) == 1, "Must not retry after emitting any chunk"


def test_anthropic_invoke_stream_overloaded_via_body_raises_friendly_error(
    monkeypatch,
) -> None:
    """APIStatusError with overloaded_error body (SSE path, no HTTP 529) raises the
    friendly overloaded message, not the raw 'APIStatusError' class name."""

    class _OverloadedBodyError(Exception):
        """Simulates APIStatusError raised from the SSE stream body (status_code absent)."""

        def __init__(self) -> None:
            super().__init__("Overloaded")
            self.body = {"error": {"type": "overloaded_error", "message": "Overloaded"}}

    def _yield_overloaded():
        raise _OverloadedBodyError()
        yield  # make it a generator

    class _Stream:
        def __init__(self) -> None:
            self.text_stream = _yield_overloaded()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Messages:
        def stream(self, **_kwargs):
            return _Stream()

    class _Anthropic:
        def __init__(self, **_kwargs) -> None:
            self.messages = _Messages()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", _Anthropic)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda _s: None)

    client = sdk_llm.LLMClient(model="claude-test")
    with pytest.raises(RuntimeError, match="overloaded"):
        list(client.invoke_stream("hi"))


def test_format_anthropic_retry_error_handles_non_dict_body_error() -> None:
    """Unexpected Anthropic body shapes should not mask the original error class."""

    class _ApiStatusError(Exception):
        body = {"error": "overloaded_error"}

    assert (
        sdk_llm._format_anthropic_retry_error(_ApiStatusError())
        == "Anthropic API request failed after multiple retries: _ApiStatusError."
    )


# ---------------------------------------------------------------------------
# OpenAILLMClient.invoke / invoke_stream — kwargs builder + streaming behavior
# ---------------------------------------------------------------------------


def _make_capturing_openai(
    *,
    response_text: str = "",
    chunk_contents: list[str | None] | None = None,
):
    """Build a fake OpenAI class that captures kwargs and returns canned data.

    ``chunk_contents`` accepts ``None`` entries to simulate empty deltas the
    real SDK emits during keep-alive — invoke_stream must skip those.
    Closure-scoped so each test gets a fresh capture dict.
    """
    state: dict = {"kwargs": None}
    raw_chunks = list(chunk_contents or [])

    class _Delta:
        def __init__(self, content: str | None) -> None:
            self.content = content

    class _Choice:
        def __init__(self, *, delta_content: str | None = None, message_content: str = "") -> None:
            self.delta = _Delta(delta_content)
            self.message = type("_Msg", (), {"content": message_content})()

    class _Response:
        def __init__(self, message_content: str) -> None:
            self.choices = [_Choice(message_content=message_content)]

    class _StreamChunk:
        def __init__(self, content: str | None) -> None:
            self.choices = [_Choice(delta_content=content)] if content is not None else []

    class _Completions:
        def create(self, **kwargs):
            state["kwargs"] = kwargs
            if kwargs.get("stream"):
                return iter(_StreamChunk(c) for c in raw_chunks)
            return _Response(response_text)

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(
            self,
            *,
            api_key: str,
            base_url: str | None = None,
            timeout: float,
            default_headers: dict[str, str] | None = None,
        ) -> None:
            self.api_key = api_key
            self.base_url = base_url
            self.timeout = timeout
            self.default_headers = default_headers
            self.chat = _Chat()

    return _OpenAI, state


def test_openai_invoke_forwards_built_kwargs_to_chat_completions_create(monkeypatch) -> None:
    """Refactored invoke() still sends model, max_tokens, and messages to the SDK."""
    fake, captured = _make_capturing_openai(response_text="hello")
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", fake)

    client = sdk_llm.OpenAILLMClient(model="gpt-test", max_tokens=64)
    response = client.invoke("hi")

    assert response.content == "hello"
    assert captured["kwargs"]["model"] == "gpt-test"
    assert captured["kwargs"]["max_tokens"] == 64
    assert captured["kwargs"]["messages"] == [{"role": "user", "content": "hi"}]
    assert "stream" not in captured["kwargs"]


def test_openai_invoke_bad_request_does_not_retry(monkeypatch) -> None:
    attempts: list[int] = []
    sleeps: list[float] = []

    class _Completions:
        def create(self, **_kwargs):
            attempts.append(1)
            raise _make_fake_openai_bad_request_error("invalid openai request")

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    client = sdk_llm.OpenAILLMClient(model="gpt-test")
    with pytest.raises(RuntimeError, match="request rejected"):
        client.invoke("hello")

    assert attempts == [1]
    assert sleeps == []


def test_openai_invoke_invalid_model_identifier_raises_not_found(monkeypatch) -> None:
    class _Completions:
        def create(self, **_kwargs):
            raise _make_fake_openai_bad_request_error(
                "Error code: 400 - litellm.BadRequestError: AnthropicException - "
                '{"message":"The provided model identifier is invalid."}'
            )

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)

    client = sdk_llm.OpenAILLMClient(model="relay-ops-claude-opus-4-7")
    with pytest.raises(RuntimeError, match="Check your configured model name or endpoint"):
        client.invoke("hello")


def test_openai_invoke_stream_invalid_model_identifier_raises_not_found(monkeypatch) -> None:
    class _Completions:
        def create(self, **_kwargs):
            raise _make_fake_openai_bad_request_error(
                "Error code: 400 - litellm.BadRequestError: AnthropicException - "
                '{"message":"The provided model identifier is invalid."}'
            )

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)

    client = sdk_llm.OpenAILLMClient(model="relay-ops-claude-opus-4-7")
    with pytest.raises(RuntimeError, match="Check your configured model name or endpoint"):
        list(client.invoke_stream("hello"))


def test_openai_invoke_invalid_reasoning_model_falls_back_to_toolcall(monkeypatch) -> None:
    calls: list[str] = []

    class _Choice:
        def __init__(self, content: str) -> None:
            self.message = type("_Msg", (), {"content": content})()

    class _Response:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    class _Completions:
        def create(self, **kwargs):
            calls.append(str(kwargs.get("model", "")))
            if len(calls) == 1:
                raise _make_fake_openai_bad_request_error(
                    "Error code: 400 - {'error': {'message': 'invalid model ID'}}"
                )
            return _Response("fallback-ok")

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)

    client = sdk_llm.OpenAILLMClient(
        model="gpt-5.4 mini",
        model_fallback="gpt-5.4-mini",
    )
    response = client.invoke("hello")

    assert response.content == "fallback-ok"
    assert calls == ["gpt-5.4 mini", "gpt-5.4-mini"]
    assert client._model == "gpt-5.4-mini"


def test_openai_invoke_stream_invalid_reasoning_model_falls_back_to_toolcall(monkeypatch) -> None:
    calls: list[str] = []

    class _Delta:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Choice:
        def __init__(self, content: str) -> None:
            self.delta = _Delta(content)

    class _Chunk:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    class _Completions:
        def create(self, **kwargs):
            calls.append(str(kwargs.get("model", "")))
            if len(calls) == 1:
                raise _make_fake_openai_bad_request_error(
                    "Error code: 400 - {'error': {'message': 'invalid model ID'}}"
                )
            return iter([_Chunk("fallback"), _Chunk("-ok")])

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)

    client = sdk_llm.OpenAILLMClient(
        model="gpt-5.4 mini",
        model_fallback="gpt-5.4-mini",
    )
    chunks = list(client.invoke_stream("hello"))

    assert chunks == ["fallback", "-ok"]
    assert calls == ["gpt-5.4 mini", "gpt-5.4-mini"]
    assert client._model == "gpt-5.4-mini"


def test_openai_invoke_stream_yields_delta_content_chunks(monkeypatch) -> None:
    """invoke_stream() routes through the same builder and yields delta.content in order."""
    fake, captured = _make_capturing_openai(chunk_contents=["Hel", "lo, ", "world"])
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", fake)

    client = sdk_llm.OpenAILLMClient(model="gpt-test", max_tokens=64)
    chunks = list(client.invoke_stream("hi"))

    assert chunks == ["Hel", "lo, ", "world"]
    assert captured["kwargs"]["stream"] is True
    assert captured["kwargs"]["model"] == "gpt-test"
    assert captured["kwargs"]["messages"] == [{"role": "user", "content": "hi"}]


def test_openai_invoke_stream_bad_request_does_not_retry(monkeypatch) -> None:
    attempts: list[int] = []
    sleeps: list[float] = []

    class _Completions:
        def create(self, **_kwargs):
            attempts.append(1)
            raise _make_fake_openai_bad_request_error("invalid openai stream request")

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    client = sdk_llm.OpenAILLMClient(model="gpt-test")
    with pytest.raises(RuntimeError, match="request rejected"):
        list(client.invoke_stream("hello"))

    assert attempts == [1]
    assert sleeps == []


def test_openai_invoke_stream_skips_empty_deltas_and_choiceless_chunks(monkeypatch) -> None:
    """OpenAI keep-alive frames have empty delta or no choices — those must not be yielded."""
    fake, _ = _make_capturing_openai(chunk_contents=["Hi", None, "", " there", None])
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", fake)

    client = sdk_llm.OpenAILLMClient(model="gpt-test")
    chunks = list(client.invoke_stream("hi"))

    assert chunks == ["Hi", " there"]


def test_openai_invoke_stream_retries_when_no_chunk_emitted(monkeypatch) -> None:
    """Transient failure before any chunk yields → retry succeeds."""
    attempts: list[bool] = []

    class _Delta:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Choice:
        def __init__(self, content: str) -> None:
            self.delta = _Delta(content)

    class _Chunk:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    class _Completions:
        def create(self, **_kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise RuntimeError("Overloaded")
            return iter([_Chunk("recovered")])

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda _seconds: None)

    client = sdk_llm.OpenAILLMClient(model="gpt-test")
    chunks = list(client.invoke_stream("hi"))

    assert chunks == ["recovered"]
    assert len(attempts) == 2


def test_openai_invoke_stream_does_not_retry_after_yielding(monkeypatch) -> None:
    """Mid-stream failure must propagate; retry would duplicate visible chunks."""
    attempts: list[bool] = []

    class _Delta:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Choice:
        def __init__(self, content: str) -> None:
            self.delta = _Delta(content)

    class _Chunk:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    def _yield_then_raise():
        yield _Chunk("partial")
        raise RuntimeError("connection dropped")

    class _Completions:
        def create(self, **_kwargs):
            attempts.append(True)
            return _yield_then_raise()

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda _seconds: None)

    client = sdk_llm.OpenAILLMClient(model="gpt-test")
    iterator = client.invoke_stream("hi")

    assert next(iterator) == "partial"

    raised = False
    try:
        next(iterator)
    except RuntimeError as exc:
        raised = True
        assert "connection dropped" in str(exc)

    assert raised
    assert len(attempts) == 1, "Must not retry after emitting any chunk"


# ---------------------------------------------------------------------------
# _create_llm_client — claude-code CLI provider routing
# ---------------------------------------------------------------------------


def test_create_llm_client_openai_reasoning_sets_toolcall_fallback(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4 mini")
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "gpt-5.4-mini")
    reset_llm_clients()
    try:
        client = factory.build_llm_client("reasoning")

        assert isinstance(client, sdk_llm.OpenAILLMClient)
        assert client._model == "gpt-5.4 mini"
        assert client._model_fallback == "gpt-5.4-mini"
    finally:
        reset_llm_clients()


def test_create_llm_client_openai_ignores_legacy_oauth_auth_method(monkeypatch) -> None:
    """A leftover LLM_AUTH_METHOD=oauth must not remap OpenAI onto Codex."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_AUTH_METHOD", "oauth")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4")
    reset_llm_clients()
    try:
        client = factory.build_llm_client("reasoning")

        assert isinstance(client, sdk_llm.OpenAILLMClient)
        assert client._model == "gpt-5.4"
    finally:
        reset_llm_clients()


def test_create_llm_client_deepseek_reasoning_sets_toolcall_fallback(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test")
    monkeypatch.setenv("DEEPSEEK_REASONING_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("DEEPSEEK_TOOLCALL_MODEL", "deepseek-v4-flash")
    reset_llm_clients()
    try:
        client = factory.build_llm_client("reasoning")

        assert isinstance(client, sdk_llm.OpenAILLMClient)
        assert client._model == "deepseek-v4-pro"
        assert client._model_fallback == "deepseek-v4-flash"
        assert client._base_url == "https://api.deepseek.com"
        assert client._api_key_env == "DEEPSEEK_API_KEY"
    finally:
        reset_llm_clients()


def test_create_llm_client_claude_code_wires_cli_adapter(monkeypatch) -> None:
    """Investigation uses ``_create_llm_client`` → registry → ``CLIBackedLLMClient``."""
    monkeypatch.setenv("LLM_PROVIDER", "claude-code")
    monkeypatch.delenv("CLAUDE_CODE_MODEL", raising=False)
    reset_llm_clients()
    try:
        from integrations.llm_cli.claude_code import ClaudeCodeAdapter
        from integrations.llm_cli.runner import CLIBackedLLMClient

        client = factory.build_llm_client("reasoning")

        assert isinstance(client, CLIBackedLLMClient)
        assert isinstance(client._adapter, ClaudeCodeAdapter)
    finally:
        reset_llm_clients()


def test_create_llm_client_claude_code_reads_optional_model_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "claude-code")
    monkeypatch.setenv("CLAUDE_CODE_MODEL", "claude-opus-4-7")
    reset_llm_clients()
    try:
        client = factory.build_llm_client("reasoning")

        assert client._model == "claude-opus-4-7"
    finally:
        reset_llm_clients()


def test_create_llm_client_gemini_cli_wires_cli_adapter(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini-cli")
    monkeypatch.delenv("GEMINI_CLI_MODEL", raising=False)
    reset_llm_clients()
    try:
        from integrations.llm_cli.gemini_cli import GeminiCLIAdapter
        from integrations.llm_cli.runner import CLIBackedLLMClient

        client = factory.build_llm_client("reasoning")

        assert isinstance(client, CLIBackedLLMClient)
        assert isinstance(client._adapter, GeminiCLIAdapter)
    finally:
        reset_llm_clients()


def test_create_llm_client_gemini_cli_reads_optional_model_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini-cli")
    monkeypatch.setenv("GEMINI_CLI_MODEL", "gemini-2.5-pro")
    reset_llm_clients()
    try:
        client = factory.build_llm_client("reasoning")

        assert client._model == "gemini-2.5-pro"
    finally:
        reset_llm_clients()


# _create_llm_client — missing API key raises RuntimeError (not ValidationError)
# ---------------------------------------------------------------------------


def test_create_llm_client_missing_api_key_raises_runtime_error(monkeypatch) -> None:
    """Sentry #1678: missing API key must surface as RuntimeError, not pydantic.ValidationError."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("config.llm_auth.credentials.resolve_api_key_env_for_request", lambda _: "")
    reset_llm_clients()
    try:
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            factory.build_llm_client("reasoning")
    finally:
        reset_llm_clients()


def test_create_llm_client_missing_api_key_omits_pydantic_boilerplate(monkeypatch) -> None:
    """Sentry #1815: the RuntimeError message must not include pydantic boilerplate."""
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("config.llm_auth.credentials.resolve_api_key_env_for_request", lambda _: "")
    reset_llm_clients()
    try:
        with pytest.raises(RuntimeError) as exc_info:
            factory.build_llm_client("reasoning")
        msg = str(exc_info.value)
        assert "1 validation error for LLMSettings" not in msg
        assert "MINIMAX_API_KEY" in msg
    finally:
        reset_llm_clients()


# ---------------------------------------------------------------------------
# LLMClient.invoke / invoke_stream — NotFoundError handling
# ---------------------------------------------------------------------------


def _make_not_found_error() -> Exception:
    """Return a minimal fake that looks like anthropic.NotFoundError."""
    err = NotFoundError.__new__(NotFoundError)
    # NotFoundError is an APIStatusError; bypass its __init__ by setting attrs directly.
    err.status_code = 404  # type: ignore[attr-defined]
    err.message = "model_not_found"  # type: ignore[attr-defined]
    err.body = {}  # type: ignore[attr-defined]
    err.request = None  # type: ignore[attr-defined]
    err.response = None  # type: ignore[attr-defined]
    return err


class _NotFoundMessages:
    """Fake messages object that always raises NotFoundError."""

    def __init__(self, model: str) -> None:
        self._model = model

    def create(self, **_kwargs) -> None:
        raise _make_not_found_error()

    def stream(self, **_kwargs):
        raise _make_not_found_error()


class _NotFoundAnthropic:
    def __init__(self, *, api_key: str, timeout: float) -> None:
        del api_key, timeout
        self.messages = _NotFoundMessages("not-a-real-model-xyz")


def test_anthropic_invoke_not_found_raises_friendly_runtime_error(monkeypatch) -> None:
    """NotFoundError from the Anthropic API must become a clear RuntimeError."""
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", _NotFoundAnthropic)

    client = sdk_llm.LLMClient(model="not-a-real-model-xyz")

    with pytest.raises(RuntimeError, match="not-a-real-model-xyz") as exc_info:
        client.invoke("hello")

    msg = str(exc_info.value)
    assert "was not found" in msg
    assert "Check your configured model name" in msg


def test_anthropic_invoke_not_found_does_not_retry(monkeypatch) -> None:
    """NotFoundError must never be retried — it is a config error, not transient."""
    call_count = 0

    class _CountingMessages:
        def create(self, **_kwargs) -> None:
            nonlocal call_count
            call_count += 1
            raise _make_not_found_error()

    class _CountingAnthropic:
        def __init__(self, **_kwargs) -> None:
            self.messages = _CountingMessages()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", _CountingAnthropic)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda _: None)

    client = sdk_llm.LLMClient(model="bad-model")

    with pytest.raises(RuntimeError):
        client.invoke("hello")

    assert call_count == 1, "NotFoundError must not trigger retries"


def test_anthropic_invoke_stream_not_found_raises_friendly_runtime_error(monkeypatch) -> None:
    """NotFoundError during streaming must also surface a clear message."""
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", _NotFoundAnthropic)

    client = sdk_llm.LLMClient(model="not-a-real-model-xyz")

    with pytest.raises(RuntimeError, match="not-a-real-model-xyz") as exc_info:
        list(client.invoke_stream("hello"))

    msg = str(exc_info.value)
    assert "was not found" in msg
    assert "Check your configured model name" in msg


# ---------------------------------------------------------------------------
# OpenAILLMClient.invoke — RateLimitError handling
#
# Retry-delay extraction (Gemini retryDelay, headers, body text) is unit-tested
# in tests/utils/test_llm_retry.py against the shared extract_retry_after_seconds.
# ---------------------------------------------------------------------------


class _FakeRateLimitError(OpenAIRateLimitError):
    """Minimal stand-in for openai.RateLimitError with a Google-style body.

    Inherits from the real class so it's caught by ``except OpenAIRateLimitError``.
    Calls ``Exception.__init__`` directly to bypass ``APIStatusError.__init__``,
    which requires a live ``request``/``response`` pair not available in unit tests.
    This also ensures ``str(err)`` works correctly via ``args``.
    """

    def __init__(self, retry_delay: str) -> None:
        Exception.__init__(self, f"quota exceeded, retry in {retry_delay}")
        self.status_code = 429
        self.body = {"error": {"details": [{"retryDelay": retry_delay}]}}


def _make_fake_rate_limit_error(retry_delay: str = "5s") -> Exception:
    """Return a fake openai.RateLimitError carrying a Google-style retry body."""
    return _FakeRateLimitError(retry_delay)


def test_openai_invoke_rate_limit_retries_with_suggested_delay(monkeypatch) -> None:
    """On RateLimitError, invoke() sleeps for the suggested delay and retries."""
    attempts: list[int] = []
    sleeps: list[float] = []

    class _Choice:
        def __init__(self, content: str) -> None:
            self.message = type("_Msg", (), {"content": content})()

    class _Response:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    class _Completions:
        def create(self, **_kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise _make_fake_rate_limit_error("6s")
            return _Response("ok")

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    client = sdk_llm.OpenAILLMClient(model="gemini-test", api_key_env="GEMINI_API_KEY")
    result = client.invoke("hi")

    assert result.content == "ok"
    assert len(attempts) == 2
    # Must sleep for the suggested 6s (>= initial backoff of 1s)
    assert sleeps == [6.0]


def test_openai_invoke_rate_limit_raises_quota_message_after_exhaustion(monkeypatch) -> None:
    """After all retries on RateLimitError, raise a quota-specific RuntimeError."""
    sleeps: list[float] = []

    class _Completions:
        def create(self, **_kwargs):
            raise _make_fake_rate_limit_error("5s")

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    client = sdk_llm.OpenAILLMClient(model="gemini-test", api_key_env="GEMINI_API_KEY")

    with pytest.raises(RuntimeError) as exc_info:
        client.invoke("hi")

    msg = str(exc_info.value)
    assert "rate limit" in msg.lower()
    assert "quota" in msg.lower()


def test_openai_invoke_stream_rate_limit_retries_before_emit(monkeypatch) -> None:
    """RateLimitError before any chunk is emitted should retry with the suggested delay."""
    attempts: list[int] = []
    sleeps: list[float] = []

    class _Delta:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Choice:
        def __init__(self, content: str) -> None:
            self.delta = _Delta(content)

    class _Chunk:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    class _Completions:
        def create(self, **_kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise _make_fake_rate_limit_error("7s")
            return iter([_Chunk("recovered")])

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    client = sdk_llm.OpenAILLMClient(model="gemini-test", api_key_env="GEMINI_API_KEY")
    chunks = list(client.invoke_stream("hi"))

    assert chunks == ["recovered"]
    assert len(attempts) == 2
    assert sleeps == [7.0]


class _FakeQuotaError(OpenAIRateLimitError):
    """Fake RateLimitError with a billing-quota error code."""

    def __init__(self, code: str = "insufficient_quota") -> None:
        Exception.__init__(self, "You have no credits remaining")
        self.status_code = 429
        self.body = {
            "error": {
                "message": "You have no credits remaining. Add credits to continue using the API.",
                "type": "insufficient_quota",
                "code": code,
            }
        }


_FakeInsufficientQuotaError = _FakeQuotaError


def test_openai_invoke_hosted_credit_exhaustion_preserves_upgrade_url(
    monkeypatch,
) -> None:
    """A generic HTTP 402 from the OpenSRE proxy stops without retrying."""
    from core.llm.shared.llm_retry import OpenSRECreditsExhaustedError

    call_count = 0
    upgrade_url = "https://app.opensre.dev/usage"

    class _HostedCreditError(Exception):
        code = "opensre_credits_exhausted"
        body = {
            "code": "opensre_credits_exhausted",
            "upgrade_url": "https://app.opensre.dev/usage",
        }

    class _Completions:
        def create(self, **_kwargs):
            nonlocal call_count
            call_count += 1
            raise _HostedCreditError("Payment required")

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)
    sleeps: list[float] = []
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda seconds: sleeps.append(seconds))

    client = sdk_llm.OpenAILLMClient(model="gpt-4", api_key_env="OPENAI_API_KEY")
    with pytest.raises(OpenSRECreditsExhaustedError) as excinfo:
        client.invoke("hi")

    assert call_count == 1
    assert sleeps == []
    assert excinfo.value.upgrade_url == upgrade_url


def test_openai_invoke_rate_limit_insufficient_quota_raises_immediately(monkeypatch) -> None:
    """insufficient_quota (billing limit) must raise RuntimeError without retrying."""
    call_count = 0

    class _Completions:
        def create(self, **_kwargs):
            nonlocal call_count
            call_count += 1
            raise _FakeInsufficientQuotaError()

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)
    sleeps: list[float] = []
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    client = sdk_llm.OpenAILLMClient(model="gpt-4", api_key_env="OPENAI_API_KEY")
    with pytest.raises(RuntimeError) as exc_info:
        client.invoke("hi")

    assert call_count == 1, "insufficient_quota must not be retried"
    assert sleeps == [], "insufficient_quota must not sleep before raising"
    msg = str(exc_info.value).lower()
    assert "quota" in msg or "billing" in msg


def test_openai_invoke_rate_limit_credit_balance_exhausted_raises_immediately(
    monkeypatch,
) -> None:
    """``credit_balance_exhausted`` is prepaid-empty, not a retryable 429."""
    call_count = 0

    class _Completions:
        def create(self, **_kwargs):
            nonlocal call_count
            call_count += 1
            raise _FakeQuotaError("credit_balance_exhausted")

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)
    sleeps: list[float] = []
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    client = sdk_llm.OpenAILLMClient(model="gpt-4", api_key_env="OPENAI_API_KEY")
    with pytest.raises(RuntimeError) as exc_info:
        client.invoke("hi")

    assert call_count == 1
    assert sleeps == []
    assert "quota" in str(exc_info.value).lower() or "billing" in str(exc_info.value).lower()


def test_openai_invoke_stream_rate_limit_insufficient_quota_raises_immediately(
    monkeypatch,
) -> None:
    """insufficient_quota in invoke_stream() must raise immediately without retry."""
    call_count = 0

    class _Completions:
        def create(self, **_kwargs):
            nonlocal call_count
            call_count += 1
            raise _FakeInsufficientQuotaError()

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)
    sleeps: list[float] = []
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    client = sdk_llm.OpenAILLMClient(model="gpt-4", api_key_env="OPENAI_API_KEY")
    with pytest.raises(RuntimeError) as exc_info:
        list(client.invoke_stream("hi"))

    assert call_count == 1, "insufficient_quota must not be retried in invoke_stream"
    assert sleeps == []
    msg = str(exc_info.value).lower()
    assert "quota" in msg or "billing" in msg


def test_openai_invoke_stream_rate_limit_insufficient_quota_after_emit_is_wrapped(
    monkeypatch,
) -> None:
    """insufficient_quota after a streamed token still uses the friendly quota error."""
    call_count = 0

    class _Delta:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Choice:
        def __init__(self, content: str) -> None:
            self.delta = _Delta(content)

    class _Chunk:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    class _Completions:
        def create(self, **_kwargs):
            nonlocal call_count
            call_count += 1

            def _stream():
                yield _Chunk("partial")
                raise _FakeInsufficientQuotaError()

            return _stream()

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)
    sleeps: list[float] = []
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    client = sdk_llm.OpenAILLMClient(model="gpt-4", api_key_env="OPENAI_API_KEY")
    stream = client.invoke_stream("hi")

    assert next(stream) == "partial"
    with pytest.raises(RuntimeError) as exc_info:
        next(stream)

    assert call_count == 1, "mid-stream insufficient_quota must not be retried"
    assert sleeps == []
    msg = str(exc_info.value).lower()
    assert "quota" in msg or "billing" in msg


# ─────────────────────────────────────────────────────────────────────────────
# OpenAILLMClient – APITimeoutError retry handling
# ─────────────────────────────────────────────────────────────────────────────


class _FakeTimeoutError(OpenAITimeoutError):
    """Minimal stand-in for openai.APITimeoutError."""

    def __init__(self) -> None:
        super().__init__(request=None)  # type: ignore[arg-type]


def test_openai_invoke_timeout_retries_and_succeeds(monkeypatch) -> None:
    """APITimeoutError on the first attempt must be retried; success on the second."""
    attempts: list[int] = []
    sleeps: list[float] = []

    class _Choice:
        def __init__(self, content: str) -> None:
            self.message = type("_Msg", (), {"content": content})()

    class _Response:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    class _Completions:
        def create(self, **_kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise _FakeTimeoutError()
            return _Response("ok")

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    client = sdk_llm.OpenAILLMClient(model="gpt-4", api_key_env="OPENAI_API_KEY")
    result = client.invoke("hi")

    assert result.content == "ok"
    assert len(attempts) == 2
    assert len(sleeps) == 1


def test_openai_invoke_timeout_raises_timeout_message_after_exhaustion(monkeypatch) -> None:
    """After all retries on APITimeoutError, raise a RuntimeError with a timeout message."""
    sleeps: list[float] = []

    class _Completions:
        def create(self, **_kwargs):
            raise _FakeTimeoutError()

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    client = sdk_llm.OpenAILLMClient(model="gpt-4", api_key_env="OPENAI_API_KEY")
    with pytest.raises(RuntimeError) as exc_info:
        client.invoke("hi")

    msg = str(exc_info.value).lower()
    assert "timed out" in msg or "timeout" in msg
    assert "network connection" not in msg
    assert len(sleeps) == _RETRY_MAX_ATTEMPTS - 1


def test_openai_invoke_stream_timeout_retries_before_emit(monkeypatch) -> None:
    """APITimeoutError before any chunk is emitted should be retried."""
    attempts: list[int] = []
    sleeps: list[float] = []

    class _Delta:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Choice:
        def __init__(self, content: str) -> None:
            self.delta = _Delta(content)

    class _Chunk:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    class _Completions:
        def create(self, **_kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise _FakeTimeoutError()

            def _stream():
                yield _Chunk("hello")

            return _stream()

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    client = sdk_llm.OpenAILLMClient(model="gpt-4", api_key_env="OPENAI_API_KEY")
    chunks = list(client.invoke_stream("hi"))

    assert chunks == ["hello"]
    assert len(attempts) == 2
    assert len(sleeps) == 1


def test_openai_invoke_stream_timeout_does_not_retry_after_emit(monkeypatch) -> None:
    """APITimeoutError mid-stream (after a chunk was emitted) must not be retried."""
    call_count = 0
    sleeps: list[float] = []

    class _Delta:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Choice:
        def __init__(self, content: str) -> None:
            self.delta = _Delta(content)

    class _Chunk:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    class _Completions:
        def create(self, **_kwargs):
            nonlocal call_count
            call_count += 1

            def _stream():
                yield _Chunk("partial")
                raise _FakeTimeoutError()

            return _stream()

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _OpenAI)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    client = sdk_llm.OpenAILLMClient(model="gpt-4", api_key_env="OPENAI_API_KEY")
    stream = client.invoke_stream("hi")
    assert next(stream) == "partial"
    with pytest.raises(OpenAITimeoutError):
        next(stream)

    assert call_count == 1, "mid-stream timeout must not be retried"
    assert sleeps == []


# ─────────────────────────────────────────────────────────────────────────────
# BedrockLLMClient – non-transient error handling
# ─────────────────────────────────────────────────────────────────────────────


def _make_anthropic_http_response(status_code: int, body: dict) -> object:
    """Return a minimal fake httpx.Response accepted by the anthropic SDK error types."""
    import json

    class _FakeRequest:
        method = "POST"
        url = "https://bedrock.example.com"

    _sc = status_code
    _body = body

    class _FakeResponse:
        request = _FakeRequest()
        status_code = _sc
        headers: dict = {}

        def json(self):
            return _body

        def read(self):
            return json.dumps(_body).encode()

        def iter_lines(self):
            return iter([])

    return _FakeResponse()


def _make_bedrock_anthropic_client(exc: Exception) -> object:
    """Return a fake AnthropicBedrock whose messages.create() raises *exc*."""

    class _Messages:
        def create(self, **_kwargs):
            raise exc

    class _Client:
        messages = _Messages()

    return _Client()


class _InactiveGuardrailEvaluator:
    is_active = False

    def __call__(self):
        return self


def test_bedrock_invoke_anthropic_not_found_raises_immediately(monkeypatch) -> None:
    """NotFoundError (EOL model) must raise RuntimeError without retrying."""
    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    sleeps: list[float] = []
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    resp = _make_anthropic_http_response(
        404, {"error": {"type": "not_found_error", "message": "not found"}}
    )
    err = NotFoundError(message="not found", response=resp, body={})  # type: ignore[arg-type]
    monkeypatch.setattr(
        sdk_llm, "AnthropicBedrock", lambda **_: _make_bedrock_anthropic_client(err)
    )

    client = sdk_llm.BedrockLLMClient(model="anthropic.claude-old-v1:0")
    with pytest.raises(RuntimeError, match="end-of-life"):
        client.invoke("hello")

    assert sleeps == [], "non-transient errors must not be retried"


def test_bedrock_invoke_anthropic_authentication_raises_immediately(monkeypatch) -> None:
    """AuthenticationError must raise RuntimeError without retrying."""
    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    sleeps: list[float] = []
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    resp = _make_anthropic_http_response(
        401,
        {"error": {"type": "authentication_error", "message": "bad credentials"}},
    )
    err = AuthenticationError(message="bad credentials", response=resp, body={})  # type: ignore[arg-type]
    monkeypatch.setattr(
        sdk_llm, "AnthropicBedrock", lambda **_: _make_bedrock_anthropic_client(err)
    )

    client = sdk_llm.BedrockLLMClient(model="anthropic.claude-test")
    with pytest.raises(RuntimeError, match="authentication failed"):
        client.invoke("hello")

    assert sleeps == [], "non-transient errors must not be retried"


def test_bedrock_invoke_anthropic_bad_request_inference_profile(monkeypatch) -> None:
    """BadRequestError with 'on-demand throughput' hint must suggest inference profile."""
    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    sleeps: list[float] = []
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    resp = _make_anthropic_http_response(
        400,
        {
            "error": {
                "type": "invalid_request_error",
                "message": "on-demand throughput isn't supported",
            }
        },
    )
    err = AnthropicBadRequestError(
        message="on-demand throughput isn't supported", response=resp, body={}
    )  # type: ignore[arg-type]
    monkeypatch.setattr(
        sdk_llm, "AnthropicBedrock", lambda **_: _make_bedrock_anthropic_client(err)
    )

    client = sdk_llm.BedrockLLMClient(model="anthropic.claude-opus-4-1-20250805-v1:0")
    with pytest.raises(RuntimeError, match="inference profile"):
        client.invoke("hello")

    assert sleeps == [], "non-transient errors must not be retried"


def test_bedrock_invoke_anthropic_permission_denied_raises_immediately(monkeypatch) -> None:
    """PermissionDeniedError must raise RuntimeError without retrying."""
    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    sleeps: list[float] = []
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    resp = _make_anthropic_http_response(
        403,
        {"error": {"type": "permission_error", "message": "not available for this account"}},
    )
    err = PermissionDeniedError(message="not available for this account", response=resp, body={})  # type: ignore[arg-type]
    monkeypatch.setattr(
        sdk_llm, "AnthropicBedrock", lambda **_: _make_bedrock_anthropic_client(err)
    )

    client = sdk_llm.BedrockLLMClient(model="anthropic.claude-opus-4-7")
    with pytest.raises(RuntimeError, match="not available for your account"):
        client.invoke("hello")

    assert sleeps == [], "non-transient errors must not be retried"


def test_bedrock_invoke_converse_validation_exception_raises_immediately(monkeypatch) -> None:
    """ValidationException from boto3 converse must raise RuntimeError without retrying."""
    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    sleeps: list[float] = []
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    import botocore.exceptions

    boto_err = botocore.exceptions.ClientError(
        {
            "Error": {
                "Code": "ValidationException",
                "Message": "The provided model identifier is invalid.",
            }
        },
        "Converse",
    )

    class _FailingRuntime:
        def converse(self, **_kwargs):
            raise boto_err

    monkeypatch.setattr(sdk_llm.boto3, "client", lambda *_a, **_k: _FailingRuntime())

    client = sdk_llm.BedrockLLMClient(model="invalid-model-xyz")
    with pytest.raises(RuntimeError, match="invalid"):
        client.invoke("hello")

    assert sleeps == [], "non-transient errors must not be retried"


@pytest.mark.parametrize(
    "code",
    ["AccessDeniedException", "ResourceNotFoundException"],
)
def test_bedrock_invoke_converse_hard_client_errors_raise_immediately(
    monkeypatch,
    code: str,
) -> None:
    """Permanent boto3 ClientError codes must raise RuntimeError without retrying."""
    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    sleeps: list[float] = []
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda s: sleeps.append(s))

    import botocore.exceptions

    boto_err = botocore.exceptions.ClientError(
        {
            "Error": {
                "Code": code,
                "Message": "Bedrock hard failure.",
            }
        },
        "Converse",
    )

    class _FailingRuntime:
        def converse(self, **_kwargs):
            raise boto_err

    monkeypatch.setattr(sdk_llm.boto3, "client", lambda *_a, **_k: _FailingRuntime())

    client = sdk_llm.BedrockLLMClient(model="mistral.some-model")
    with pytest.raises(RuntimeError):
        client.invoke("hello")

    assert sleeps == [], "non-transient errors must not be retried"


def test_bedrock_access_denied_surfaces_upstream_aws_message(monkeypatch) -> None:
    """``AccessDeniedException`` on Bedrock can also indicate an AWS
    Marketplace billing problem (e.g. ``INVALID_PAYMENT_INSTRUMENT``) or a
    missing per-model Bedrock opt-in, not just IAM. The wrapped
    ``RuntimeError`` must include the upstream AWS ``Message`` so the user
    knows which one to fix. Regression coverage for #1808."""
    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda _s: None)

    import botocore.exceptions

    aws_message = (
        "Model access is denied due to INVALID_PAYMENT_INSTRUMENT:"
        "A valid payment instrument must be provided."
    )
    boto_err = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": aws_message}},
        "Converse",
    )

    class _FailingRuntime:
        def converse(self, **_kwargs):
            raise boto_err

    monkeypatch.setattr(sdk_llm.boto3, "client", lambda *_a, **_k: _FailingRuntime())

    client = sdk_llm.BedrockLLMClient(model="some-model")
    with pytest.raises(RuntimeError) as excinfo:
        client.invoke("hello")

    rendered = str(excinfo.value)
    assert "INVALID_PAYMENT_INSTRUMENT" in rendered
    assert "payment instrument" in rendered.lower()
    assert "AWS Marketplace" in rendered


def test_bedrock_access_denied_without_payment_keywords_shows_iam_checklist(
    monkeypatch,
) -> None:
    """Other AccessDenied messages keep the broader Bedrock/IAM/marketplace checklist."""
    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda _s: None)

    import botocore.exceptions

    aws_message = "User: arn:aws:iam::123:user/x is not authorized to perform: bedrock:InvokeModel"
    boto_err = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": aws_message}},
        "Converse",
    )

    class _FailingRuntime:
        def converse(self, **_kwargs):
            raise boto_err

    monkeypatch.setattr(sdk_llm.boto3, "client", lambda *_a, **_k: _FailingRuntime())

    client = sdk_llm.BedrockLLMClient(model="some-model")
    with pytest.raises(RuntimeError) as excinfo:
        client.invoke("hello")

    rendered = str(excinfo.value)
    assert aws_message in rendered
    assert "Bedrock model access" in rendered
    assert "IAM permissions" in rendered


def test_format_openai_connection_error_ssl_via_cause() -> None:
    """SSL fingerprint in __cause__ triggers the TLS-specific message."""
    ssl_err = Exception("[SSL: WRONG_VERSION_NUMBER] wrong version number")
    conn_err = Exception("Connection error.")
    conn_err.__cause__ = ssl_err

    msg = sdk_llm._format_openai_connection_error(conn_err, "OpenAI")

    assert "SSL/TLS" in msg
    assert "HTTPS" in msg


def test_format_openai_connection_error_ssl_via_context() -> None:
    """SSL fingerprint buried in __context__ also triggers the TLS-specific message."""
    ssl_err = Exception("certificate verify failed")
    conn_err = Exception("Connection error.")
    conn_err.__cause__ = None
    conn_err.__context__ = ssl_err

    msg = sdk_llm._format_openai_connection_error(conn_err, "Gemini")

    assert "SSL/TLS" in msg
    assert "Gemini" in msg


def test_format_openai_connection_error_non_ssl_returns_generic_message() -> None:
    """A plain connection-refused error lands on the generic network message."""
    conn_err = Exception("[WinError 10061] connection refused")

    msg = sdk_llm._format_openai_connection_error(conn_err, "NVIDIA")

    assert "SSL" not in msg
    assert "network connection" in msg
    assert "NVIDIA" in msg


def test_format_openai_connection_error_timeout_returns_timeout_message() -> None:
    """APITimeoutError gets a specific timeout message, not the generic network-connection one."""
    err = OpenAITimeoutError.__new__(OpenAITimeoutError)
    Exception.__init__(err, "Request timed out.")

    msg = sdk_llm._format_openai_connection_error(err, "Ollama")

    assert "timed out" in msg.lower()
    assert "Ollama" in msg
    assert "network connection" not in msg


# ---------------------------------------------------------------------------
# extract_json_payload — embedded code-fence handling (Sentry #1861)
# ---------------------------------------------------------------------------


def test_extract_json_payload_bare_json() -> None:
    assert extract_json_payload('{"a": 1}') == {"a": 1}


def test_extract_json_payload_leading_fence() -> None:
    text = '```json\n{"a": 1}\n```'
    assert extract_json_payload(text) == {"a": 1}


def test_extract_json_payload_embedded_fence_with_preamble() -> None:
    """LLM returns prose before the code block — Sentry #1861 root cause."""
    text = 'Here is the JSON:\n\n```json\n{"location": "node.py"}\n```'
    assert extract_json_payload(text) == {"location": "node.py"}


def test_extract_json_payload_embedded_fence_trailing_braces_in_prose() -> None:
    """Greedy regex would over-capture {key} in trailing prose; fence path must win."""
    text = 'Sure!\n\n```json\n{"key": "value"}\n```\n\nThe {key} field represents the identifier.'
    assert extract_json_payload(text) == {"key": "value"}


def test_extract_json_payload_raises_when_no_json() -> None:
    with pytest.raises(ValueError, match="LLM did not return valid JSON payload"):
        extract_json_payload("This is plain text with no JSON.")


# LLMClient.invoke / invoke_stream — usage-limit BadRequestError (HTTP 400) handling


_USAGE_LIMIT_BODY = {
    "type": "error",
    "error": {
        "type": "invalid_request_error",
        "message": "You have reached your specified API usage limits. You will regain access on 2026-06-01 at 00:00 UTC.",
    },
}


def _make_bad_request_error(body: dict | None = None) -> Exception:
    err = AnthropicBadRequestError.__new__(AnthropicBadRequestError)
    err.status_code = 400  # type: ignore[attr-defined]
    err.message = str(body)  # type: ignore[attr-defined]
    err.body = body or {}  # type: ignore[attr-defined]
    err.request = None  # type: ignore[attr-defined]
    err.response = None  # type: ignore[attr-defined]
    return err


class _BadRequestMessages:
    def __init__(self, body: dict) -> None:
        self._body = body

    def create(self, **_kwargs) -> None:
        raise _make_bad_request_error(self._body)

    def stream(self, **_kwargs):
        raise _make_bad_request_error(self._body)


class _UsageLimitAnthropic:
    def __init__(self, *, api_key: str, timeout: float) -> None:
        del api_key, timeout
        self.messages = _BadRequestMessages(_USAGE_LIMIT_BODY)


def test_anthropic_invoke_usage_limit_raises_friendly_runtime_error(monkeypatch) -> None:
    """HTTP 400 usage-limit error must surface a clear message, not a raw SDK repr."""
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", _UsageLimitAnthropic)

    client = sdk_llm.LLMClient(model="claude-3-5-sonnet-20241022")

    with pytest.raises(RuntimeError) as exc_info:
        client.invoke("hello")

    msg = str(exc_info.value)
    assert "usage limit" in msg.lower()
    assert "You will regain access" in msg


def test_anthropic_invoke_usage_limit_does_not_retry(monkeypatch) -> None:
    """BadRequestError must never be retried — it is a permanent client error."""
    call_count = 0

    class _CountingMessages:
        def create(self, **_kwargs) -> None:
            nonlocal call_count
            call_count += 1
            raise _make_bad_request_error(_USAGE_LIMIT_BODY)

    class _CountingAnthropic:
        def __init__(self, **_kwargs) -> None:
            self.messages = _CountingMessages()

    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", _CountingAnthropic)
    monkeypatch.setattr(sdk_llm.time, "sleep", lambda _: None)

    client = sdk_llm.LLMClient(model="claude-3-5-sonnet-20241022")

    with pytest.raises(RuntimeError):
        client.invoke("hello")

    assert call_count == 1, "BadRequestError must not trigger retries"


def test_anthropic_invoke_stream_usage_limit_raises_friendly_runtime_error(monkeypatch) -> None:
    """HTTP 400 usage-limit during streaming must also surface a clear message."""
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", _UsageLimitAnthropic)

    client = sdk_llm.LLMClient(model="claude-3-5-sonnet-20241022")

    with pytest.raises(RuntimeError) as exc_info:
        list(client.invoke_stream("hello"))

    msg = str(exc_info.value)
    assert "usage limit" in msg.lower()


def test_anthropic_invoke_bad_request_non_usage_limit_raises_generic_message(monkeypatch) -> None:
    """Non-usage-limit HTTP 400 errors fall back to a generic HTTP 400 message."""
    other_body = {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "Invalid JSON in request."},
    }
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )

    class _OtherBadRequestAnthropic:
        def __init__(self, **_kwargs) -> None:
            self.messages = _BadRequestMessages(other_body)

    monkeypatch.setattr(sdk_llm, "Anthropic", _OtherBadRequestAnthropic)

    client = sdk_llm.LLMClient(model="claude-3-5-sonnet-20241022")

    with pytest.raises(RuntimeError) as exc_info:
        client.invoke("hello")

    msg = str(exc_info.value)
    assert "HTTP 400" in msg


# ─────────────────────────────────────────────────────────────────────────────
# Usage hook — observer wired by the benchmark framework
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_usage_hook():
    """Guard tests against hook leakage from other tests in the file."""
    usage_mod.set_usage_hook(None)
    yield
    usage_mod.set_usage_hook(None)


def _make_recording_hook() -> tuple[list[tuple[str, int, int]], usage_mod.UsageHook]:
    """Return (calls_list, hook). Closure-factory keeps state isolated per test."""
    calls: list[tuple[str, int, int]] = []

    def hook(model: str, tokens_in: int, tokens_out: int) -> None:
        calls.append((model, tokens_in, tokens_out))

    return calls, hook


def test_usage_hook_anthropic_invoke_fires_with_correct_token_counts(monkeypatch) -> None:
    class _Usage:
        input_tokens = 123
        output_tokens = 45

    class _Block:
        type = "text"
        text = "ok"

    class _Response:
        content = [_Block()]
        usage = _Usage()

    class _Messages:
        def create(self, **_kwargs):
            return _Response()

    class _FakeAnthropicWithUsage:
        def __init__(self, **_kwargs) -> None:
            self.messages = _Messages()

    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", _FakeAnthropicWithUsage)

    calls, hook = _make_recording_hook()
    usage_mod.set_usage_hook(hook)

    client = sdk_llm.LLMClient(model="claude-sonnet-4-5-20250929")
    client.invoke("hi")

    assert calls == [("claude-sonnet-4-5-20250929", 123, 45)]


def test_usage_hook_openai_invoke_fires_with_correct_token_counts(monkeypatch) -> None:
    class _Usage:
        prompt_tokens = 200
        completion_tokens = 50

    class _Message:
        content = "ok"

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]
        usage = _Usage()

    class _Completions:
        def create(self, **_kwargs):
            return _Response()

    class _Chat:
        def __init__(self) -> None:
            self.completions = _Completions()

    class _FakeOpenAIWithUsage:
        def __init__(self, **_kwargs) -> None:
            self.chat = _Chat()

    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "OpenAI", _FakeOpenAIWithUsage)

    calls, hook = _make_recording_hook()
    usage_mod.set_usage_hook(hook)

    client = sdk_llm.OpenAILLMClient(model="gpt-4o-2024-11-20")
    client.invoke("hi")

    assert calls == [("gpt-4o-2024-11-20", 200, 50)]


def test_usage_hook_bedrock_converse_fires_with_correct_token_counts(monkeypatch) -> None:
    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    response = {
        "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
        "usage": {"inputTokens": 77, "outputTokens": 11},
    }
    runtime = _RecordingBedrockRuntime(response)
    monkeypatch.setattr(sdk_llm.boto3, "client", lambda *_a, **_k: runtime)

    calls, hook = _make_recording_hook()
    usage_mod.set_usage_hook(hook)

    client = sdk_llm.BedrockLLMClient(model="mistral.mistral-large-2402-v1:0")
    client.invoke([{"role": "user", "content": "hi"}])

    assert calls == [("mistral.mistral-large-2402-v1:0", 77, 11)]


def test_usage_hook_exception_propagates(monkeypatch) -> None:
    """CostBudgetExceeded raised from the hook must bubble out of invoke()
    so the benchmark runner can halt cleanly."""

    class _Usage:
        input_tokens = 1
        output_tokens = 1

    class _Block:
        type = "text"
        text = "ok"

    class _Response:
        content = [_Block()]
        usage = _Usage()

    class _Messages:
        def create(self, **_kwargs):
            return _Response()

    class _FakeAnthropicWithUsage:
        def __init__(self, **_kwargs) -> None:
            self.messages = _Messages()

    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", _FakeAnthropicWithUsage)

    class _BudgetExceeded(RuntimeError):
        pass

    def hook(_model, _tin, _tout):
        raise _BudgetExceeded("over budget")

    usage_mod.set_usage_hook(hook)

    client = sdk_llm.LLMClient(model="claude-sonnet-4-5-20250929")
    with pytest.raises(_BudgetExceeded):
        client.invoke("hi")


def test_usage_hook_unset_is_default_noop(monkeypatch) -> None:
    """Without set_usage_hook, invoke must succeed and emit nothing."""

    class _Usage:
        input_tokens = 1
        output_tokens = 1

    class _Block:
        type = "text"
        text = "ok"

    class _Response:
        content = [_Block()]
        usage = _Usage()

    class _Messages:
        def create(self, **_kwargs):
            return _Response()

    class _FakeAnthropicWithUsage:
        def __init__(self, **_kwargs) -> None:
            self.messages = _Messages()

    monkeypatch.setattr(
        "infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator",
        _InactiveGuardrailEvaluator,
    )
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "k"
    )
    monkeypatch.setattr(sdk_llm, "Anthropic", _FakeAnthropicWithUsage)

    client = sdk_llm.LLMClient(model="claude-sonnet-4-5-20250929")
    response = client.invoke("hi")
    assert response.content == "ok"


def test_set_usage_hook_rejects_double_registration() -> None:
    """The hook is a process-wide singleton — setting a real hook over an
    already-registered hook would silently overwrite, attributing future
    usage to the wrong observer. The guard makes that misuse fail loudly.
    """
    calls_a, hook_a = _make_recording_hook()
    calls_b, hook_b = _make_recording_hook()

    usage_mod.set_usage_hook(hook_a)
    try:
        with pytest.raises(RuntimeError, match="already registered"):
            usage_mod.set_usage_hook(hook_b)
    finally:
        # Confirm the first hook is still the active one and clean up
        usage_mod.set_usage_hook(None)

    assert calls_a == []
    assert calls_b == []


def test_set_usage_hook_allows_clear_then_set() -> None:
    """Clearing with None then setting a new hook is the normal lifecycle
    (between BenchmarkRunner invocations). Must not trip the guard."""
    _, hook_a = _make_recording_hook()
    _, hook_b = _make_recording_hook()

    usage_mod.set_usage_hook(hook_a)
    usage_mod.set_usage_hook(None)
    usage_mod.set_usage_hook(hook_b)  # should NOT raise
    usage_mod.set_usage_hook(None)


def test_set_usage_hook_allows_redundant_clear() -> None:
    """Setting None over None is a no-op (idempotent clear). Must not raise."""
    usage_mod.set_usage_hook(None)
    usage_mod.set_usage_hook(None)  # should NOT raise
