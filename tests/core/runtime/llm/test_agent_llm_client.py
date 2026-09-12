from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from core.llm.factory import LLMRole, get_llm, reset_llm_clients
from core.llm.transports.sdk.agent_clients import (
    AnthropicAgentClient,
    BedrockAgentClient,
    OpenAIAgentClient,
    _try_parse_tool_call_json,
)


def _install_fake_anthropic(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    fake_module = types.SimpleNamespace()

    class AuthenticationError(Exception):
        pass

    class BadRequestError(Exception):
        def __init__(self, message: str) -> None:
            super().__init__(message)
            self.message = message

    class NotFoundError(Exception):
        pass

    class PermissionDeniedError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class InternalServerError(Exception):
        def __init__(self, message: str, body: dict | None = None) -> None:
            super().__init__(message)
            self.body = body or {}

    class Anthropic:
        def __init__(self, **_: object) -> None:
            self.messages = types.SimpleNamespace(create=lambda **_: None)

    class AnthropicBedrock:
        def __init__(self, **_: object) -> None:
            self.messages = types.SimpleNamespace(create=lambda **_: None)

    fake_module.AuthenticationError = AuthenticationError
    fake_module.BadRequestError = BadRequestError
    fake_module.NotFoundError = NotFoundError
    fake_module.PermissionDeniedError = PermissionDeniedError
    fake_module.RateLimitError = RateLimitError
    fake_module.InternalServerError = InternalServerError
    fake_module.Anthropic = Anthropic
    fake_module.AnthropicBedrock = AnthropicBedrock
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    return fake_module


def test_bedrock_client_requires_region_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(monkeypatch)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    with pytest.raises(RuntimeError, match="Bedrock requires AWS_REGION or AWS_DEFAULT_REGION"):
        BedrockAgentClient(model="us.anthropic.claude-sonnet-4-6")


def test_bedrock_auth_error_message_references_aws_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_anthropic = _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    client = BedrockAgentClient(model="us.anthropic.claude-sonnet-4-6")

    def raise_auth_error(**_: object) -> object:
        raise fake_anthropic.AuthenticationError("expired")

    client._client = types.SimpleNamespace(messages=types.SimpleNamespace(create=raise_auth_error))

    with pytest.raises(RuntimeError) as exc:
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    message = str(exc.value)
    assert "Bedrock authentication failed" in message
    assert "AWS credentials" in message
    assert "ANTHROPIC_API_KEY" not in message


def test_bedrock_permission_denied_is_not_retried_and_mentions_marketplace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_anthropic = _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    client = BedrockAgentClient(model="us.anthropic.claude-sonnet-4-6")
    calls = 0

    def raise_permission_denied(**_: object) -> object:
        nonlocal calls
        calls += 1
        raise fake_anthropic.PermissionDeniedError("marketplace denied")

    client._client = types.SimpleNamespace(
        messages=types.SimpleNamespace(create=raise_permission_denied)
    )

    with pytest.raises(RuntimeError) as exc:
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    message = str(exc.value)
    assert calls == 1
    assert "Bedrock model 'us.anthropic.claude-sonnet-4-6' is not available" in message
    assert "AWS Marketplace" in message
    assert "aws-marketplace:ViewSubscriptions" in message
    assert "aws-marketplace:Subscribe" in message


def test_internal_server_error_with_model_billing_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_anthropic = _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    call_count = 0

    def raise_billing_error(**_: object) -> object:
        nonlocal call_count
        call_count += 1
        raise fake_anthropic.InternalServerError(
            "Error code: 500",
            body={"message": "模型未配置计费", "data": {"model": "claude-opus-4-7"}},
        )

    client = AnthropicAgentClient(model="claude-opus-4-7")
    client._client = types.SimpleNamespace(
        messages=types.SimpleNamespace(create=raise_billing_error)
    )

    with pytest.raises(RuntimeError) as exc:
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    assert call_count == 1, "billing error should not be retried"
    message = str(exc.value)
    assert "claude-opus-4-7" in message
    assert "billing" in message.lower() or "not configured" in message.lower()


def test_internal_server_error_without_model_data_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_anthropic = _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("core.llm.transports.sdk.agent_clients.time.sleep", lambda _: None)

    call_count = 0

    def raise_transient_error(**_: object) -> object:
        nonlocal call_count
        call_count += 1
        raise fake_anthropic.InternalServerError("Internal server error", body={})

    client = AnthropicAgentClient(model="claude-sonnet-4-6")
    client._client = types.SimpleNamespace(
        messages=types.SimpleNamespace(create=raise_transient_error)
    )

    with pytest.raises(RuntimeError, match="API failed after 3 attempts"):
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    assert call_count == 3, "transient 500 errors should be retried"


def test_anthropic_rate_limit_error_is_retried_then_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rate-limit is transient by design — retry with backoff like 500s do.
    Without retry, a single 429 mid-turn kills the whole turn.
    """
    from core.llm.shared.openai_chat_completions import _RETRY_MAX_ATTEMPTS

    fake_anthropic = _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("core.llm.transports.sdk.agent_clients.time.sleep", lambda _: None)

    call_count = 0

    def raise_rate_limit(**_: object) -> object:
        nonlocal call_count
        call_count += 1
        raise fake_anthropic.RateLimitError("slow down")

    client = AnthropicAgentClient(model="claude-sonnet-4-6")
    client._client = types.SimpleNamespace(messages=types.SimpleNamespace(create=raise_rate_limit))

    with pytest.raises(RuntimeError, match="Anthropic rate limit exceeded"):
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    assert call_count == _RETRY_MAX_ATTEMPTS, (
        f"rate limit should be retried {_RETRY_MAX_ATTEMPTS} times before giving up"
    )


def test_anthropic_invoke_strips_internal_message_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    captured: dict[str, Any] = {}

    def capture_create(**kwargs: object) -> object:
        captured.update(kwargs)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="ok")],
            usage=types.SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    client = AnthropicAgentClient(model="claude-sonnet-4-6")
    client._client = types.SimpleNamespace(messages=types.SimpleNamespace(create=capture_create))

    messages = [
        {"role": "user", "content": "alert"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "seed", "name": "n", "input": {}}],
            "_opensre_seed": True,
        },
    ]
    client.invoke(messages=messages)

    api_messages = captured["messages"]
    # Internal markers stripped; last content block may carry cache_control.
    assert api_messages[1]["role"] == "assistant"
    assert "_opensre_seed" not in api_messages[1]
    assert api_messages[1]["content"] == [
        {
            "type": "tool_use",
            "id": "seed",
            "name": "n",
            "input": {},
            "cache_control": {"type": "ephemeral"},
        }
    ]
    # Original caller messages keep markers and are not mutated with cache_control.
    assert messages[1]["_opensre_seed"] is True
    assert "cache_control" not in messages[1]["content"][0]


def test_anthropic_invoke_marks_system_and_last_tool_for_prompt_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stable tools→system prefix gets Anthropic cache_control breakpoints."""
    _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    captured: dict[str, Any] = {}

    def capture_create(**kwargs: object) -> object:
        captured.update(kwargs)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="ok")],
            usage=types.SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    client = AnthropicAgentClient(model="claude-sonnet-4-6")
    client._client = types.SimpleNamespace(messages=types.SimpleNamespace(create=capture_create))

    tools = [
        {"type": "custom", "name": "a", "description": "a", "input_schema": {"type": "object"}},
        {"type": "custom", "name": "b", "description": "b", "input_schema": {"type": "object"}},
    ]
    client.invoke(
        messages=[{"role": "user", "content": "hi"}],
        system="stable system",
        tools=tools,
    )

    system = captured["system"]
    assert isinstance(system, list)
    assert system[0]["type"] == "text"
    assert system[0]["text"] == "stable system"
    assert system[0]["cache_control"] == {"type": "ephemeral"}

    api_tools = captured["tools"]
    assert "cache_control" not in api_tools[0]
    assert api_tools[1]["cache_control"] == {"type": "ephemeral"}
    # Caller-owned tool dicts must not be mutated.
    assert "cache_control" not in tools[1]
    # One action per response: the model is asked for a single tool call.
    assert captured["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}


def test_openai_agent_client_invoke_strips_internal_message_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_openai(monkeypatch)

    captured: dict[str, Any] = {}

    def capture_create(**kwargs: object) -> object:
        captured.update(kwargs)
        return _make_fake_openai_response(content="ok")

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=capture_create))
    )
    client._model = "gpt-4o"
    client._max_tokens = 1024

    messages = [
        {"role": "user", "content": "alert"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "seed", "type": "function", "function": {"name": "n", "arguments": "{}"}},
            ],
            "_opensre_seed": True,
        },
    ]
    client.invoke(messages=messages)

    api_messages = captured["messages"]
    assert api_messages[1] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "seed", "type": "function", "function": {"name": "n", "arguments": "{}"}},
        ],
    }
    assert messages[1]["_opensre_seed"] is True


def test_anthropic_credit_balance_too_low_raises_LLMCreditExhaustedError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic surfaces "credit balance too low" as HTTP 400.
    Distinguish billing exhaustion (fatal, no retry) from real schema errors
    so the bench runner halts on first occurrence instead of wrapping into
    a generic RuntimeError that the cell loop catches as a per-cell failure."""
    from core.llm.shared.llm_retry import LLMCreditExhaustedError

    fake_anthropic = _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    call_count = 0

    def raise_credit_error(**_: object) -> object:
        nonlocal call_count
        call_count += 1
        raise fake_anthropic.BadRequestError(
            "Error code: 400 - {'error': {'message': "
            "'Your credit balance is too low to access the Anthropic API.'}}"
        )

    client = AnthropicAgentClient(model="claude-sonnet-4-6")
    client._client = types.SimpleNamespace(
        messages=types.SimpleNamespace(create=raise_credit_error)
    )

    with pytest.raises(LLMCreditExhaustedError, match="credit exhausted"):
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    # Fail fast — no retry on a dead account.
    assert call_count == 1


def test_openai_insufficient_quota_raises_LLMCreditExhaustedError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI returns insufficient_quota as HTTP 429 with body text. Even
    though it lands in our RateLimitError handler (which normally retries),
    the credit check must short-circuit to LLMCreditExhaustedError. This is
    the exact scenario that burned 1h42m on the June-3 run #2."""
    from core.llm.shared.llm_retry import LLMCreditExhaustedError

    fake_openai = _install_fake_openai(monkeypatch)

    call_count = 0

    def raise_insufficient_quota(**_: object) -> object:
        nonlocal call_count
        call_count += 1
        raise fake_openai.RateLimitError(
            "OpenAI rate limit exceeded: Error code: 429 - "
            "{'error': {'message': 'You exceeded your current quota, please check "
            "your plan and billing details.', 'code': 'insufficient_quota'}}"
        )

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=raise_insufficient_quota)
        )
    )
    client._model = "gpt-4o"
    client._max_tokens = 512

    with pytest.raises(LLMCreditExhaustedError, match="credit exhausted"):
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    # Fail fast — exactly one attempt, no retry waste.
    assert call_count == 1


def test_opensre_payment_required_raises_upgrade_error_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hosted proxy returns HTTP 402 as a generic SDK status error."""
    from core.llm.shared.llm_retry import OpenSRECreditsExhaustedError

    _install_fake_openai(monkeypatch)
    call_count = 0
    upgrade_url = "https://app.opensre.dev/usage"

    class HostedPaymentRequired(Exception):
        code = "opensre_credits_exhausted"
        body = {
            "code": "opensre_credits_exhausted",
            "upgrade_url": "https://app.opensre.dev/usage",
        }

    def raise_payment_required(**_: object) -> object:
        nonlocal call_count
        call_count += 1
        raise HostedPaymentRequired("Payment required")

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=raise_payment_required))
    )
    client._model = "gpt-4o"
    client._max_tokens = 512

    with pytest.raises(OpenSRECreditsExhaustedError) as excinfo:
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    assert call_count == 1
    assert excinfo.value.upgrade_url == upgrade_url


def test_anthropic_rate_limit_honors_retry_after_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the 429 response carries ``Retry-After``, the client should
    sleep approximately that long (modulo ±10% jitter) instead of its
    longer default backoff. Validates the integration of
    ``extract_retry_after_seconds`` into the typed rate-limit handler.
    """
    fake_anthropic = _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    sleeps: list[float] = []
    monkeypatch.setattr(
        "core.llm.transports.sdk.agent_clients.time.sleep",
        sleeps.append,
    )

    class _Resp:
        # Use a Retry-After well above any jittered backoff so the server
        # hint is unambiguously the source of the sleep duration.
        headers = {"retry-after": "0.5"}

    def raise_with_header(**_: object) -> object:
        err = fake_anthropic.RateLimitError("slow down")
        err.response = _Resp()
        raise err

    client = AnthropicAgentClient(model="claude-sonnet-4-6")
    client._client = types.SimpleNamespace(messages=types.SimpleNamespace(create=raise_with_header))

    with pytest.raises(RuntimeError, match="Anthropic rate limit exceeded"):
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    # _RETRY_MAX_ATTEMPTS=3 → 2 sleeps before the final raise.
    assert len(sleeps) == 2
    # Each sleep should be ~0.5s ± 10% jitter, NOT the deterministic
    # exponential 1.0s / 2.0s the fallback would have produced.
    for s in sleeps:
        assert 0.45 <= s <= 0.55, (
            f"sleep={s} should be ~0.5s (Retry-After) with ±10% jitter, "
            f"not deterministic exponential backoff"
        )


def test_bedrock_rate_limit_error_is_retried_then_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bedrock shares the Anthropic invoke path; 429 retry applies the same way."""
    from core.llm.shared.openai_chat_completions import _RETRY_MAX_ATTEMPTS

    fake_anthropic = _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setattr("core.llm.transports.sdk.agent_clients.time.sleep", lambda _: None)

    call_count = 0

    def raise_rate_limit(**_: object) -> object:
        nonlocal call_count
        call_count += 1
        raise fake_anthropic.RateLimitError("slow down")

    client = BedrockAgentClient(model="us.anthropic.claude-sonnet-4-6")
    client._client = types.SimpleNamespace(messages=types.SimpleNamespace(create=raise_rate_limit))

    with pytest.raises(RuntimeError, match="Bedrock rate limit exceeded"):
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    assert call_count == _RETRY_MAX_ATTEMPTS, (
        f"rate limit should be retried {_RETRY_MAX_ATTEMPTS} times before giving up"
    )


def _install_fake_openai(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    fake_module = types.SimpleNamespace()

    class AuthenticationError(Exception):
        pass

    class BadRequestError(Exception):
        pass

    class NotFoundError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class PermissionDeniedError(Exception):
        pass

    class OpenAI:
        def __init__(self, **_: object) -> None:
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=lambda **_: None)
            )

    fake_module.AuthenticationError = AuthenticationError
    fake_module.BadRequestError = BadRequestError
    fake_module.NotFoundError = NotFoundError
    fake_module.RateLimitError = RateLimitError
    fake_module.PermissionDeniedError = PermissionDeniedError
    fake_module.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_module)
    return fake_module


def _make_fake_openai_response(
    *,
    content: str = "",
    tool_calls: list[types.SimpleNamespace] | None = None,
    finish_reason: str = "stop",
    extra_msg_fields: dict | None = None,
) -> types.SimpleNamespace:
    """Build a fake OpenAI chat completion response.

    model_dump() mirrors the real SDK: every pydantic field is present,
    including the null ones (refusal, audio, function_call).  This lets
    tests verify that exclude_none=True strips those nulls before the
    dict is stored in raw_content.
    """

    def model_dump(*, exclude_none: bool = False) -> dict:
        # Simulate the full SDK field set, nulls included.
        result: dict = {
            "role": "assistant",
            "content": content or None,
            "refusal": None,  # SDK null field
            "audio": None,  # SDK null field
            "function_call": None,  # SDK null field
        }
        if tool_calls:
            result["tool_calls"] = [tc.model_dump() for tc in tool_calls]
        if extra_msg_fields:
            result.update(extra_msg_fields)
        if exclude_none:
            result = {k: v for k, v in result.items() if v is not None}
        return result

    msg = types.SimpleNamespace(
        content=content or None,
        tool_calls=tool_calls,
        model_dump=model_dump,
    )
    choice = types.SimpleNamespace(message=msg, finish_reason=finish_reason)
    return types.SimpleNamespace(choices=[choice])


def test_openai_agent_client_invoke_sets_raw_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """raw_content must be the serialized API message so providers like Gemini
    can echo back provider-specific fields (e.g. thought_signature) on the
    next turn."""
    fake_openai = _install_fake_openai(monkeypatch)

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    fake_response = _make_fake_openai_response(content="hello")
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda **_: fake_response)
        )
    )
    client._model = "gemini-2.5-flash"
    client._max_tokens = 1024

    del fake_openai  # unused; just ensures the fake module is in sys.modules

    response = client.invoke(messages=[{"role": "user", "content": "hi"}])

    assert response.raw_content is not None
    assert isinstance(response.raw_content, dict)
    assert response.raw_content.get("role") == "assistant"
    # exclude_none=True must strip SDK null fields so they don't
    # cause 400s on Gemini's strict endpoint on the next turn.
    assert "refusal" not in response.raw_content
    assert "audio" not in response.raw_content
    assert "function_call" not in response.raw_content


def test_openai_agent_client_invoke_raw_content_preserves_extra_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extra fields from the provider (e.g. Gemini thought_signature inside a
    tool call) must survive through raw_content into the next turn's message."""
    _install_fake_openai(monkeypatch)

    def fake_tc_model_dump() -> dict:
        return {
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_logs", "arguments": "{}"},
            "thought_signature": "abc123",  # Gemini extension
        }

    fake_tc = types.SimpleNamespace(
        id="call_1",
        function=types.SimpleNamespace(name="get_logs", arguments="{}"),
        model_dump=fake_tc_model_dump,
    )
    fake_response = _make_fake_openai_response(tool_calls=[fake_tc])

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda **_: fake_response)
        )
    )
    client._model = "gemini-2.5-flash"
    client._max_tokens = 1024

    response = client.invoke(messages=[{"role": "user", "content": "hi"}])

    assert response.raw_content is not None
    assert isinstance(response.raw_content.get("tool_calls"), list)
    first_tc = response.raw_content["tool_calls"][0]
    assert first_tc.get("thought_signature") == "abc123"


def test_openai_agent_client_disables_parallel_tool_calls_for_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_openai(monkeypatch)
    captured: dict[str, object] = {}

    def capture_create(**kwargs: object) -> object:
        captured.update(kwargs)
        return _make_fake_openai_response(content="ok")

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=capture_create))
    )
    client._model = "gpt-5.4-mini"
    client._max_tokens = 4096
    client._api_key_env = "OPENAI_API_KEY"

    client.invoke(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "one", "parameters": {}}}],
    )

    assert captured["tool_choice"] == "auto"
    assert captured["parallel_tool_calls"] is False


def test_openai_gpt_5_6_agent_uses_responses_api_and_replays_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_openai(monkeypatch)
    monkeypatch.setenv("OPENSRE_REASONING_EFFORT", "high")
    captured: list[dict[str, Any]] = []

    reasoning = types.SimpleNamespace(
        type="reasoning",
        model_dump=lambda **_: {"id": "rs_1", "type": "reasoning", "summary": []},
    )
    function_call = types.SimpleNamespace(
        type="function_call",
        call_id="call_1",
        name="get_logs",
        arguments='{"service":"api"}',
        model_dump=lambda **_: {
            "id": "fc_1",
            "type": "function_call",
            "call_id": "call_1",
            "name": "get_logs",
            "arguments": '{"service":"api"}',
        },
    )
    first_response = types.SimpleNamespace(
        output=[reasoning, function_call],
        output_text="",
        usage=None,
    )
    final_response = types.SimpleNamespace(
        output=[],
        output_text="done",
        usage=None,
    )

    def responses_create(**kwargs: Any) -> object:
        captured.append(kwargs)
        return first_response if len(captured) == 1 else final_response

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = types.SimpleNamespace(
        responses=types.SimpleNamespace(create=responses_create),
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(
                create=lambda **_: pytest.fail("GPT-5.6 must not use Chat Completions")
            )
        ),
    )
    client._model = "gpt-5.6"
    client._max_tokens = 4096
    client._api_key_env = "OPENAI_API_KEY"
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_logs",
                "description": "Fetch logs",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    first = client.invoke(
        messages=[{"role": "user", "content": "check the api"}],
        tools=tools,
    )
    second = client.invoke(
        messages=[
            {"role": "user", "content": "check the api"},
            first.raw_content,
            {"role": "tool", "tool_call_id": "call_1", "content": '{"ok":true}'},
        ],
        tools=tools,
    )

    assert first.tool_calls[0].id == "call_1"
    assert first.tool_calls[0].input == {"service": "api"}
    assert second.content == "done"
    assert captured[0]["max_output_tokens"] == 4096
    assert captured[0]["parallel_tool_calls"] is False
    assert captured[0]["reasoning"] == {"effort": "high"}
    assert captured[0]["tools"] == [
        {
            "type": "function",
            "name": "get_logs",
            "description": "Fetch logs",
            "parameters": {"type": "object", "properties": {}},
            "strict": False,
        }
    ]
    assert captured[1]["input"][1:3] == [
        {"id": "rs_1", "type": "reasoning", "summary": []},
        {
            "id": "fc_1",
            "type": "function_call",
            "call_id": "call_1",
            "name": "get_logs",
            "arguments": '{"service":"api"}',
        },
    ]
    assert captured[1]["input"][3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"ok":true}',
    }


def test_openai_agent_client_omits_parallel_tool_calls_for_compat_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_openai(monkeypatch)
    captured: dict[str, object] = {}

    def capture_create(**kwargs: object) -> object:
        captured.update(kwargs)
        return _make_fake_openai_response(content="ok")

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=capture_create))
    )
    client._model = "gemini-3.1-flash-lite-preview"
    client._max_tokens = 4096
    client._api_key_env = "GEMINI_API_KEY"

    client.invoke(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "one", "parameters": {}}}],
    )

    assert captured["tool_choice"] == "auto"
    assert "parallel_tool_calls" not in captured


def test_openai_o_series_uses_max_completion_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """o-series and gpt-5 series models must receive max_completion_tokens, not max_tokens."""
    _install_fake_openai(monkeypatch)

    captured: dict = {}

    def capture_create(**kwargs: object) -> object:
        captured.update(kwargs)
        return _make_fake_openai_response(content="ok")

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=capture_create))
    )
    client._max_tokens = 4096

    for model in (
        "o1",
        "o1-mini",
        "o3",
        "o3-mini",
        "o4-mini",
        "openai/o4-mini",
        "azure/o3",
        "my-o1-deployment",
        "gpt-5",
        "gpt-5o",
        "gpt-5o-mini",
    ):
        captured.clear()
        client._model = model
        client.invoke(messages=[{"role": "user", "content": "hi"}])
        assert "max_completion_tokens" in captured, f"{model} should use max_completion_tokens"
        assert "max_tokens" not in captured, f"{model} must not send max_tokens"


def test_openai_standard_models_use_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-o-series models must still receive max_tokens."""
    _install_fake_openai(monkeypatch)

    captured: dict = {}

    def capture_create(**kwargs: object) -> object:
        captured.update(kwargs)
        return _make_fake_openai_response(content="ok")

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=capture_create))
    )
    client._max_tokens = 4096

    for model in ("gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gemini-2.5-flash"):
        captured.clear()
        client._model = model
        client.invoke(messages=[{"role": "user", "content": "hi"}])
        assert "max_tokens" in captured, f"{model} should use max_tokens"
        assert "max_completion_tokens" not in captured, (
            f"{model} must not send max_completion_tokens"
        )


def test_openai_rate_limit_error_is_retried_then_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rate-limit is transient by design — retry with backoff like 500s do.
    Without retry, a single 429 mid-turn kills the whole turn (matters
    especially on tight OpenAI tiers like gpt-4o's 30k TPM).
    """
    from core.llm.shared.openai_chat_completions import _RETRY_MAX_ATTEMPTS

    fake_openai = _install_fake_openai(monkeypatch)
    monkeypatch.setattr("core.llm.transports.sdk.agent_clients.time.sleep", lambda _: None)

    call_count = 0

    def raise_rate_limit(**_: object) -> object:
        nonlocal call_count
        call_count += 1
        raise fake_openai.RateLimitError("slow down")

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=raise_rate_limit))
    )
    client._model = "gpt-4o"
    client._max_tokens = 512

    with pytest.raises(RuntimeError, match="OpenAI rate limit exceeded"):
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    assert call_count == _RETRY_MAX_ATTEMPTS, (
        f"429 should be retried {_RETRY_MAX_ATTEMPTS} times before giving up"
    )


def test_openai_rate_limit_honors_body_text_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI's 429 body usually says ``"Please try again in NNms"``. The
    client should sleep for that suggested duration (± 10% jitter), not
    its deterministic backoff."""
    fake_openai = _install_fake_openai(monkeypatch)

    sleeps: list[float] = []
    monkeypatch.setattr(
        "core.llm.transports.sdk.agent_clients.time.sleep",
        sleeps.append,
    )

    def raise_with_body_hint(**_: object) -> object:
        # 250ms hint — distinct from the 1s/2s deterministic backoff so
        # we can tell which path produced the sleep value.
        raise fake_openai.RateLimitError(
            "OpenAI rate limit exceeded: Error code: 429 - "
            "Limit 30000, Used 29248. Please try again in 250ms."
        )

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=raise_with_body_hint))
    )
    client._model = "gpt-4o"
    client._max_tokens = 512

    with pytest.raises(RuntimeError, match="OpenAI rate limit exceeded"):
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    assert len(sleeps) == 2
    for s in sleeps:
        assert 0.225 <= s <= 0.275, (
            f"sleep={s} should be ~0.25s (body hint) with ±10% jitter, "
            f"not deterministic exponential backoff"
        )


def test_openai_permission_denied_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_openai = _install_fake_openai(monkeypatch)
    monkeypatch.setattr("core.llm.transports.sdk.agent_clients.time.sleep", lambda _: None)

    call_count = 0

    def raise_permission_denied(**_: object) -> object:
        nonlocal call_count
        call_count += 1
        raise fake_openai.PermissionDeniedError("forbidden")

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=raise_permission_denied)
        )
    )
    client._model = "gpt-4o"
    client._max_tokens = 512

    with pytest.raises(RuntimeError, match="OpenAI request forbidden"):
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    assert call_count == 1, "403 should not retry"


def test_openai_agent_client_reports_configured_provider_name_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Errors from an OpenAI-compatible provider (e.g. DeepSeek) must be
    reported under that provider's name, not a hardcoded "OpenAI" prefix —
    see GH issue #3753."""
    fake_openai = _install_fake_openai(monkeypatch)

    def raise_insufficient_balance(**_: object) -> object:
        raise fake_openai.BadRequestError(
            "Error code: 402 - {'error': {'message': 'Insufficient Balance'}}"
        )

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=raise_insufficient_balance)
        )
    )
    client._model = "deepseek-v4-pro"
    client._max_tokens = 512
    client._api_key_env = "DEEPSEEK_API_KEY"

    with pytest.raises(RuntimeError, match="Deepseek request rejected"):
        client.invoke(messages=[{"role": "user", "content": "hi"}])


@pytest.mark.parametrize(
    ("api_key_env", "expected_label"),
    [
        ("OPENAI_API_KEY", "OpenAI"),
        ("OPENROUTER_API_KEY", "OpenRouter"),
        ("MINIMAX_API_KEY", "MiniMax"),
        ("GEMINI_API_KEY", "Gemini"),
        ("GROQ_API_KEY", "Groq"),
        ("NVIDIA_API_KEY", "Nvidia"),
    ],
)
def test_openai_agent_client_provider_label_preserves_brand_casing(
    api_key_env: str, expected_label: str
) -> None:
    """.title() mangles brand names with an internal capital letter (e.g.
    "OpenRouter" -> "Openrouter", "MiniMax" -> "Minimax"). Providers this
    class is actually constructed with (see
    core/llm/providers/openai_compat_providers.py) must keep their canonical casing."""
    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._api_key_env = api_key_env

    assert client._provider_label == expected_label


def test_sdk_type_error_for_missing_api_key_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    call_count = 0

    def raise_auth_type_error(**_: object) -> object:
        nonlocal call_count
        call_count += 1
        raise TypeError(
            "Could not resolve authentication method. Expected one of api_key, auth_token, "
            "or credentials to be set. Or for one of the `X-Api-Key` or `Authorization` "
            "headers to be explicitly omitted"
        )

    client = AnthropicAgentClient(model="claude-sonnet-4-6")
    client._client = types.SimpleNamespace(
        messages=types.SimpleNamespace(create=raise_auth_type_error)
    )

    with pytest.raises(RuntimeError) as exc:
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    assert call_count == 1, "auth TypeError should not be retried"
    message = str(exc.value)
    assert "authentication failed" in message.lower()
    assert "ANTHROPIC_API_KEY" in message


def test_unrelated_type_error_is_retried_and_wrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("core.llm.transports.sdk.agent_clients.time.sleep", lambda _: None)

    call_count = 0

    def raise_unrelated_type_error(**_: object) -> object:
        nonlocal call_count
        call_count += 1
        raise TypeError("unexpected argument 'foo'")

    client = AnthropicAgentClient(model="claude-sonnet-4-6")
    client._client = types.SimpleNamespace(
        messages=types.SimpleNamespace(create=raise_unrelated_type_error)
    )

    with pytest.raises(RuntimeError, match="API failed after 3 attempts"):
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    assert call_count == 3, "non-auth TypeError should be retried like a generic exception"


def test_build_openai_tool_specs_preserves_additional_properties_false() -> None:
    from core.llm.shared.tool_schema_normalize import build_openai_tool_specs

    tool = types.SimpleNamespace(
        name="strict_object_tool",
        description="tool with closed object schema",
        public_input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )

    parameters = build_openai_tool_specs([tool])[0]["function"]["parameters"]
    assert parameters["additionalProperties"] is False


def test_build_openai_tool_specs_normalizes_anyof_optional_parameters() -> None:
    from core.llm.shared.tool_schema_normalize import build_openai_tool_specs

    tool = types.SimpleNamespace(
        name="optional_field_tool",
        description="tool with optional field",
        public_input_schema={
            "type": "object",
            "properties": {
                "required_field": {"type": "string"},
                "optional_field": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["required_field"],
        },
    )

    specs = build_openai_tool_specs([tool])
    parameters = specs[0]["function"]["parameters"]
    assert parameters["type"] == "object"
    optional_field = parameters["properties"]["optional_field"]
    assert optional_field == {"type": "string"}, "anyOf/nullable must be flattened away"


def test_get_llm_agent_routes_deepseek_to_openai_compatible_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeOpenAIAgentClient:
        def __init__(
            self,
            model: str,
            max_tokens: int = 4096,
            base_url: str | None = None,
            api_key_env: str = "OPENAI_API_KEY",
            api_key_default: str = "",
        ) -> None:
            captured.update(
                {
                    "model": model,
                    "max_tokens": max_tokens,
                    "base_url": base_url,
                    "api_key_env": api_key_env,
                    "api_key_default": api_key_default,
                }
            )

    monkeypatch.setattr(
        "core.llm.transports.sdk.agent_clients.OpenAIAgentClient", _FakeOpenAIAgentClient
    )
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test")
    monkeypatch.setenv("DEEPSEEK_REASONING_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("DEEPSEEK_TOOLCALL_MODEL", "deepseek-v4-flash")

    reset_llm_clients()
    client = get_llm(LLMRole.AGENT)

    assert isinstance(client, _FakeOpenAIAgentClient)
    assert captured["model"] == "deepseek-v4-pro"
    assert captured["base_url"] == "https://api.deepseek.com"
    assert captured["api_key_env"] == "DEEPSEEK_API_KEY"


def test_get_llm_agent_routes_deepseek_to_litellm_when_transport_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.llm.transports.litellm.clients import LiteLLMAgentClient

    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test")
    monkeypatch.setenv("DEEPSEEK_REASONING_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("OPENSRE_LLM_TRANSPORT", "litellm")

    reset_llm_clients()
    client = get_llm(LLMRole.AGENT)

    assert isinstance(client, LiteLLMAgentClient)
    assert client._litellm_model == "openai/deepseek-v4-pro"
    assert client._api_base == "https://api.deepseek.com"
    assert client._api_key_env == "DEEPSEEK_API_KEY"


def test_get_llm_agent_uses_sdk_without_transport_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test")
    monkeypatch.setenv("DEEPSEEK_REASONING_MODEL", "deepseek-v4-pro")
    monkeypatch.delenv("OPENSRE_LLM_TRANSPORT", raising=False)

    reset_llm_clients()
    client = get_llm(LLMRole.AGENT)

    assert isinstance(client, OpenAIAgentClient)


@pytest.mark.parametrize(
    "provider",
    [
        "codex",
        "opencode",
        "claude-code",
        "kimi",
        "cursor",
        "gemini-cli",
        "antigravity-cli",
        "copilot",
    ],
)
def test_get_llm_agent_returns_cli_backed_client_for_cli_providers(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.llm.transports.sdk.agent_clients import (
        CLIBackedAgentClient,
    )

    monkeypatch.setenv("LLM_PROVIDER", provider)
    reset_llm_clients()
    client = get_llm(LLMRole.AGENT)
    assert isinstance(client, CLIBackedAgentClient), (
        f"Expected CLIBackedAgentClient for provider={provider!r}, got {type(client).__name__}"
    )


def test_get_llm_agent_openai_ignores_legacy_oauth_auth_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.llm.transports.sdk.agent_clients import OpenAIAgentClient

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_AUTH_METHOD", "oauth")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4")

    reset_llm_clients()
    client = get_llm(LLMRole.AGENT)

    assert isinstance(client, OpenAIAgentClient)


def test_cli_backed_agent_client_tool_call_parsing() -> None:
    """CLIBackedAgentClient correctly parses a JSON tool_calls response."""
    import types as _types

    from core.llm.transports.sdk.agent_clients import CLIBackedAgentClient

    fake_adapter = _types.SimpleNamespace(
        name="codex",
        binary_env_key="CODEX_BIN",
        install_hint="npm i -g @openai/codex",
        auth_hint="codex login",
        default_exec_timeout_sec=30.0,
        detect=lambda: _types.SimpleNamespace(
            installed=True, bin_path="/usr/bin/codex", logged_in=True, detail=""
        ),
        build=lambda **kw: _types.SimpleNamespace(
            argv=("/usr/bin/codex", "exec", "-"),
            stdin=kw.get("prompt", ""),
            cwd="/",
            env=None,
            timeout_sec=30.0,
        ),
        parse=lambda **kw: kw.get("stdout", ""),
        explain_failure=lambda **kw: f"exit {kw.get('returncode')}",
    )
    client = CLIBackedAgentClient(fake_adapter, model=None)

    # Patch CLIBackedLLMClient.invoke to return a known JSON response.
    import unittest.mock as mock

    from core.llm.types import LLMResponse

    json_response = '{"tool_calls": [{"id": "c1", "name": "my_tool", "input": {"x": 1}}]}'
    with mock.patch(
        "integrations.llm_cli.runner.CLIBackedLLMClient.invoke",
        return_value=LLMResponse(content=json_response),
    ):
        result = client.invoke([{"role": "user", "content": "investigate"}])

    assert result.has_tool_calls
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "my_tool"
    assert result.tool_calls[0].input == {"x": 1}
    assert result.content == ""


def test_cli_backed_agent_client_build_assistant_message_includes_tool_json() -> None:
    """Assistant history must retain tool_calls JSON for multi-turn CLI prompts."""
    from core.llm.transports.sdk.agent_clients import CLIBackedAgentClient
    from core.llm.types import ToolCall

    msg = CLIBackedAgentClient.build_assistant_message(
        "",
        [ToolCall(id="t1", name="query_logs", input={"q": "error"})],
    )
    assert msg["role"] == "assistant"
    assert "query_logs" in msg["content"]
    assert '"tool_calls"' in msg["content"]
    assert "t1" in msg["content"]


def test_try_parse_tool_call_json_uses_raw_decode_not_greedy_brace_span() -> None:
    """Trailing brace-containing prose after valid JSON must not drop tool_calls."""

    text = '{"tool_calls": [{"id": "a", "name": "t1", "input": {}}]} Here\'s context: {not json}'
    parsed = _try_parse_tool_call_json(text)
    assert parsed is not None
    assert len(parsed["tool_calls"]) == 1
    assert parsed["tool_calls"][0]["name"] == "t1"


def test_try_parse_tool_call_json_recovers_when_unfenced_preamble_precedes_json() -> None:
    """Unfenced prose before JSON should still allow tool_calls extraction."""

    text = 'Reasoning preamble {draft}\n{"tool_calls": [{"id": "a", "name": "t1", "input": {}}]}'
    parsed = _try_parse_tool_call_json(text)
    assert parsed is not None
    assert len(parsed["tool_calls"]) == 1
    assert parsed["tool_calls"][0]["name"] == "t1"


def test_cli_backed_agent_client_accepts_plain_text_as_final_answer() -> None:
    from core.llm.transports.sdk.agent_clients import CLIBackedAgentClient

    class _FakeCLI:
        def invoke(self, prompt: str) -> object:
            _ = prompt
            return type("Response", (), {"content": "Direct answer"})()

    client = CLIBackedAgentClient.__new__(CLIBackedAgentClient)
    client._cli_client = _FakeCLI()
    result = client.invoke([{"role": "user", "content": "what is this tool?"}], tools=[])

    assert result.content == "Direct answer"
    assert result.tool_calls == []


def test_cli_backed_agent_client_parses_tool_json() -> None:
    from core.llm.transports.sdk.agent_clients import CLIBackedAgentClient

    class _FakeCLI:
        def invoke(self, prompt: str) -> object:
            _ = prompt
            return type(
                "Response",
                (),
                {
                    "content": (
                        '{"tool_calls": [{"id": "c1", "name": "my_tool", "input": {"x": 1}}]}'
                    )
                },
            )()

    client = CLIBackedAgentClient.__new__(CLIBackedAgentClient)
    client._cli_client = _FakeCLI()
    result = client.invoke(
        [{"role": "user", "content": "investigate"}],
        tools=[{"name": "my_tool", "input_schema": {"type": "object"}}],
    )

    assert result.content == ""
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "my_tool"
    assert result.tool_calls[0].input == {"x": 1}


def test_cli_backed_agent_client_reuses_single_cli_llm_client() -> None:
    """CLIBackedLLMClient should be constructed once so probe cache spans invokes."""
    import types as _types
    import unittest.mock as mock

    from core.llm.transports.sdk.agent_clients import CLIBackedAgentClient
    from core.llm.types import LLMResponse
    from integrations.llm_cli.runner import CLIBackedLLMClient

    fake_adapter = _types.SimpleNamespace(
        name="codex",
        binary_env_key="CODEX_BIN",
        install_hint="",
        auth_hint="codex login",
        default_exec_timeout_sec=30.0,
        detect=lambda: _types.SimpleNamespace(
            installed=True, bin_path="/usr/bin/codex", logged_in=True, detail=""
        ),
        build=lambda **_kw: _types.SimpleNamespace(
            argv=("/usr/bin/codex",), stdin="", cwd="/", env=None, timeout_sec=30.0
        ),
        parse=lambda **_kw: "",
        explain_failure=lambda **_kw: "",
    )
    real_init = CLIBackedLLMClient.__init__
    init_count = {"n": 0}

    def counting_init(self: Any, *args: Any, **kwargs: Any) -> None:
        init_count["n"] += 1
        return real_init(self, *args, **kwargs)

    with mock.patch.object(CLIBackedLLMClient, "__init__", counting_init):
        client = CLIBackedAgentClient(fake_adapter, model=None)
        with mock.patch.object(
            CLIBackedLLMClient, "invoke", return_value=LLMResponse(content="ok")
        ):
            client.invoke([{"role": "user", "content": "a"}])
            client.invoke([{"role": "user", "content": "b"}])

    assert init_count["n"] == 1


def test_cli_backed_agent_client_plain_text_response() -> None:
    """CLIBackedAgentClient treats non-JSON output as a final text answer."""
    import types as _types
    import unittest.mock as mock

    from core.llm.transports.sdk.agent_clients import CLIBackedAgentClient
    from core.llm.types import LLMResponse

    fake_adapter = _types.SimpleNamespace(
        name="codex",
        binary_env_key="CODEX_BIN",
        install_hint="",
        auth_hint="codex login",
        default_exec_timeout_sec=30.0,
        detect=lambda: _types.SimpleNamespace(
            installed=True, bin_path="/usr/bin/codex", logged_in=True, detail=""
        ),
        build=lambda **_kw: _types.SimpleNamespace(
            argv=("/usr/bin/codex",), stdin="", cwd="/", env=None, timeout_sec=30.0
        ),
        parse=lambda **_kw: "",
        explain_failure=lambda **_kw: "",
    )
    client = CLIBackedAgentClient(fake_adapter, model=None)

    with mock.patch(
        "integrations.llm_cli.runner.CLIBackedLLMClient.invoke",
        return_value=LLMResponse(content="The root cause is a memory leak."),
    ):
        result = client.invoke([{"role": "user", "content": "summarise"}])

    assert not result.has_tool_calls
    assert result.content == "The root cause is a memory leak."


def test_cli_backed_agent_client_invalid_tool_json_falls_back_to_text_response() -> None:
    """Malformed tool_calls payload should not erase the model's textual response."""
    import types as _types
    import unittest.mock as mock

    from core.llm.transports.sdk.agent_clients import CLIBackedAgentClient
    from core.llm.types import LLMResponse

    fake_adapter = _types.SimpleNamespace(
        name="codex",
        binary_env_key="CODEX_BIN",
        install_hint="",
        auth_hint="codex login",
        default_exec_timeout_sec=30.0,
        detect=lambda: _types.SimpleNamespace(
            installed=True, bin_path="/usr/bin/codex", logged_in=True, detail=""
        ),
        build=lambda **_kw: _types.SimpleNamespace(
            argv=("/usr/bin/codex",), stdin="", cwd="/", env=None, timeout_sec=30.0
        ),
        parse=lambda **_kw: "",
        explain_failure=lambda **_kw: "",
    )
    client = CLIBackedAgentClient(fake_adapter, model=None)
    raw = '{"tool_calls":"not-a-list"}'

    with mock.patch(
        "integrations.llm_cli.runner.CLIBackedLLMClient.invoke",
        return_value=LLMResponse(content=raw),
    ):
        result = client.invoke([{"role": "user", "content": "summarise"}])

    assert not result.has_tool_calls
    assert result.content == raw


def test_cli_backed_agent_client_filtered_tool_calls_fall_back_to_text_response() -> None:
    """If all parsed tool calls are filtered out, preserve text content."""
    import types as _types
    import unittest.mock as mock

    from core.llm.transports.sdk.agent_clients import CLIBackedAgentClient
    from core.llm.types import LLMResponse

    fake_adapter = _types.SimpleNamespace(
        name="codex",
        binary_env_key="CODEX_BIN",
        install_hint="",
        auth_hint="codex login",
        default_exec_timeout_sec=30.0,
        detect=lambda: _types.SimpleNamespace(
            installed=True, bin_path="/usr/bin/codex", logged_in=True, detail=""
        ),
        build=lambda **_kw: _types.SimpleNamespace(
            argv=("/usr/bin/codex",), stdin="", cwd="/", env=None, timeout_sec=30.0
        ),
        parse=lambda **_kw: "",
        explain_failure=lambda **_kw: "",
    )
    client = CLIBackedAgentClient(fake_adapter, model=None)
    raw = '{"tool_calls":[{"id":"c1","name":"   ","input":{"x":1}}]}'

    with mock.patch(
        "integrations.llm_cli.runner.CLIBackedLLMClient.invoke",
        return_value=LLMResponse(content=raw),
    ):
        result = client.invoke([{"role": "user", "content": "summarise"}])

    assert not result.has_tool_calls
    assert result.content == raw


def test_bedrock_bad_request_cross_region_inference_gives_helpful_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_anthropic = _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    client = BedrockAgentClient(model="anthropic.claude-sonnet-4-20250514-v1:0")
    bedrock_error_body = (
        "Error code: 400 - {'message': \"Invocation of model ID "
        "anthropic.claude-sonnet-4-20250514-v1:0 with on-demand throughput isn't supported. "
        'Retry your request with the ID or ARN of an inference profile that contains this model."}'
    )

    def raise_bad_request(**_: object) -> object:
        raise fake_anthropic.BadRequestError(bedrock_error_body)

    client._client = types.SimpleNamespace(messages=types.SimpleNamespace(create=raise_bad_request))

    with pytest.raises(RuntimeError) as exc:
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    message = str(exc.value)
    assert "requires a cross-region inference profile" in message
    assert "Try prefixing with 'us.'" in message
    assert "BEDROCK_REASONING_MODEL" in message


def test_bedrock_bad_request_generic_error_uses_default_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_anthropic = _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    client = BedrockAgentClient(model="us.anthropic.claude-sonnet-4-6")

    def raise_bad_request(**_: object) -> object:
        raise fake_anthropic.BadRequestError("content policy violation")

    client._client = types.SimpleNamespace(messages=types.SimpleNamespace(create=raise_bad_request))

    with pytest.raises(RuntimeError) as exc:
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    message = str(exc.value)
    assert "Bedrock request rejected (HTTP 400)" in message
    assert "cross-region" not in message


def test_openai_unexpected_response_type_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When an OpenAI-compatible provider returns a non-ChatCompletion object
    invoke() must raise RuntimeError instead of an opaque AttributeError."""
    _install_fake_openai(monkeypatch)

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda **_: "unexpected string response")
        )
    )
    client._model = "some-provider/model"
    client._max_tokens = 512

    with pytest.raises(RuntimeError, match="unexpected response"):
        client.invoke(messages=[{"role": "user", "content": "hi"}])


def test_openai_empty_choices_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the provider returns a response with an empty choices list
    invoke() must raise RuntimeError."""
    _install_fake_openai(monkeypatch)

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda **_: types.SimpleNamespace(choices=[]))
        )
    )
    client._model = "some-provider/model"
    client._max_tokens = 512

    with pytest.raises(RuntimeError, match="unexpected response"):
        client.invoke(messages=[{"role": "user", "content": "hi"}])


_MISTRAL_MODEL = "mistral.mistral-large-3-675b-instruct"


def _make_converse_response(
    *,
    text: str = "",
    tool_uses: list[dict[str, object]] | None = None,
    stop_reason: str = "end_turn",
) -> dict[str, object]:
    content: list[dict[str, object]] = []
    if text:
        content.append({"text": text})
    for tool_use in tool_uses or []:
        content.append({"toolUse": tool_use})
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop_reason,
    }


def _stub_boto3_converse(
    monkeypatch: pytest.MonkeyPatch,
    *,
    converse_response: dict[str, object] | None = None,
    converse_side_effect: Exception | None = None,
) -> None:
    def converse(**_: object) -> dict[str, object]:
        if converse_side_effect is not None:
            raise converse_side_effect
        return converse_response or _make_converse_response(text="ok")

    monkeypatch.setitem(
        sys.modules,
        "boto3",
        types.SimpleNamespace(
            client=lambda *_args, **_kwargs: types.SimpleNamespace(converse=converse)
        ),
    )


def test_bedrock_converse_requires_region_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    _stub_boto3_converse(monkeypatch)

    from core.llm.transports.sdk.agent_clients import BedrockConverseAgentClient

    with pytest.raises(RuntimeError, match="Bedrock requires AWS_REGION or AWS_DEFAULT_REGION"):
        BedrockConverseAgentClient(model=_MISTRAL_MODEL)


def test_bedrock_converse_invoke_parses_tool_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    _stub_boto3_converse(
        monkeypatch,
        converse_response=_make_converse_response(
            text="Checking.",
            tool_uses=[{"toolUseId": "tu1", "name": "query_logs", "input": {"q": "err"}}],
            stop_reason="tool_use",
        ),
    )

    from core.llm.transports.sdk.agent_clients import BedrockConverseAgentClient

    result = BedrockConverseAgentClient(model=_MISTRAL_MODEL).invoke(
        messages=[{"role": "user", "content": [{"text": "hi"}]}]
    )
    assert result.content == "Checking."
    assert result.has_tool_calls
    assert result.tool_calls[0].id == "tu1"
    assert result.stop_reason == "tool_use"


def test_get_llm_agent_routes_non_anthropic_bedrock_to_converse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_REASONING_MODEL", _MISTRAL_MODEL)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    _stub_boto3_converse(monkeypatch)

    from core.llm.transports.sdk.agent_clients import (
        BedrockConverseAgentClient,
    )

    reset_llm_clients()
    assert isinstance(get_llm(LLMRole.AGENT), BedrockConverseAgentClient)


def test_get_llm_agent_routes_anthropic_bedrock_to_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_REASONING_MODEL", "us.anthropic.claude-sonnet-4-6")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    from core.llm.transports.sdk.agent_clients import (
        BedrockAgentClient,
    )

    reset_llm_clients()
    assert isinstance(get_llm(LLMRole.AGENT), BedrockAgentClient)


def test_bedrock_converse_throttling_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """ThrottlingException must be retried, not hard-failed on first occurrence."""
    import botocore.exceptions

    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr("time.sleep", lambda _: None)

    throttle_err = botocore.exceptions.ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
        "Converse",
    )
    call_count = 0

    def converse(**_: object) -> dict[str, object]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise throttle_err
        return _make_converse_response(text="ok")

    monkeypatch.setitem(
        sys.modules,
        "boto3",
        types.SimpleNamespace(
            client=lambda *_args, **_kwargs: types.SimpleNamespace(converse=converse)
        ),
    )

    from core.llm.transports.sdk.agent_clients import BedrockConverseAgentClient

    result = BedrockConverseAgentClient(model=_MISTRAL_MODEL).invoke(
        messages=[{"role": "user", "content": [{"text": "hi"}]}]
    )
    assert result.content == "ok"
    assert call_count == 2


def test_bedrock_converse_throttling_all_retries_exhausted_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After all retries are exhausted on ThrottlingException, a clear error is raised."""
    import botocore.exceptions

    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr("time.sleep", lambda _: None)

    throttle_err = botocore.exceptions.ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
        "Converse",
    )

    def always_throttle(**_: object) -> dict[str, object]:
        raise throttle_err

    monkeypatch.setitem(
        sys.modules,
        "boto3",
        types.SimpleNamespace(
            client=lambda *_args, **_kwargs: types.SimpleNamespace(converse=always_throttle)
        ),
    )

    from core.llm.transports.sdk.agent_clients import BedrockConverseAgentClient

    with pytest.raises(RuntimeError, match="rate limit"):
        BedrockConverseAgentClient(model=_MISTRAL_MODEL).invoke(
            messages=[{"role": "user", "content": [{"text": "hi"}]}]
        )


def test_anthropic_unexpected_response_shape_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the Anthropic SDK returns something other than a Message (e.g. a bare string),
    AnthropicAgentClient.invoke() should raise RuntimeError instead of AttributeError."""
    _install_fake_anthropic(monkeypatch)
    client = AnthropicAgentClient(
        model="claude-opus-4-7",
        client=types.SimpleNamespace(
            messages=types.SimpleNamespace(create=lambda **_: "unexpected string response")
        ),
    )

    with pytest.raises(RuntimeError, match="unexpected response"):
        client.invoke(messages=[{"role": "user", "content": "hello"}])


def test_anthropic_agent_client_emits_provider_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful agent invokes must report provider token usage via the usage hook
    so agent-turn telemetry can carry real token counts (issue #3698)."""
    from core.llm.shared.usage import set_usage_hook

    _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    response = types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text="hi there")],
        stop_reason="end_turn",
        usage=types.SimpleNamespace(input_tokens=123, output_tokens=45),
    )
    client = AnthropicAgentClient(model="claude-sonnet-4-6")
    client._client = types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **_: response)
    )

    usage: list[tuple[str, int, int]] = []
    set_usage_hook(
        lambda model, tokens_in, tokens_out: usage.append((model, tokens_in, tokens_out))
    )
    try:
        result = client.invoke(messages=[{"role": "user", "content": "hi"}])
    finally:
        set_usage_hook(None)

    assert result.content == "hi there"
    assert usage == [("claude-sonnet-4-6", 123, 45)]


def test_bedrock_converse_emits_provider_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.llm.shared.usage import set_usage_hook

    monkeypatch.setenv("AWS_REGION", "us-east-1")
    response = _make_converse_response(text="ok")
    response["usage"] = {"inputTokens": 200, "outputTokens": 30}
    _stub_boto3_converse(monkeypatch, converse_response=response)

    from core.llm.transports.sdk.agent_clients import BedrockConverseAgentClient

    usage: list[tuple[str, int, int]] = []
    set_usage_hook(
        lambda model, tokens_in, tokens_out: usage.append((model, tokens_in, tokens_out))
    )
    try:
        result = BedrockConverseAgentClient(model=_MISTRAL_MODEL).invoke(
            messages=[{"role": "user", "content": [{"text": "hi"}]}]
        )
    finally:
        set_usage_hook(None)

    assert result.content == "ok"
    assert usage == [(_MISTRAL_MODEL, 200, 30)]
