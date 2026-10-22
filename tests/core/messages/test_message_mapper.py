"""Unit tests for MessageMapper — ingress, pre-layer, and post-layer."""

from __future__ import annotations

import builtins
import json
import subprocess
import sys
from typing import Any

import pytest

from core.llm.types import AgentLLMResponse, ToolCall
from core.messages import MessageMapper
from core.messages.runtime_message_types import (
    AppRuntimeMessage,
    AssistantRuntimeMessage,
    RuntimeMessage,
    ToolResultRuntimeMessage,
    UserRuntimeMessage,
)

# ---------------------------------------------------------------------------
# Minimal fake LLM that falls through all isinstance checks
# ---------------------------------------------------------------------------


class _FakeLLM:
    """Duck-typed LLM client — NOT a subclass of any real provider."""

    def build_assistant_message(self, content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [{"id": tc.id, "name": tc.name} for tc in tool_calls],
        }

    def build_tool_result_message(
        self, tool_calls: list[ToolCall], results: list[Any]
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "results": [{"id": tc.id, "output": out} for tc, out in zip(tool_calls, results)],
        }


# ---------------------------------------------------------------------------
# Ingress — MessageMapper.to_runtime_messages
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_passthrough_runtime_message(self) -> None:
        msg = UserRuntimeMessage(content="hello")
        result = MessageMapper.to_runtime_messages([msg])
        assert result == [msg]

    def test_user_role_dict(self) -> None:
        result = MessageMapper.to_runtime_messages([{"role": "user", "content": "hi"}])
        assert len(result) == 1
        assert isinstance(result[0], UserRuntimeMessage)
        assert result[0].content == "hi"

    def test_assistant_role_dict_stores_payload(self) -> None:
        payload = {"role": "assistant", "content": "ok"}
        result = MessageMapper.to_runtime_messages([payload])
        assert isinstance(result[0], AssistantRuntimeMessage)
        assert result[0].provider_payload == payload

    def test_tool_role_dict(self) -> None:
        result = MessageMapper.to_runtime_messages(
            [{"role": "tool", "name": "my_tool", "content": "out"}]
        )
        assert isinstance(result[0], ToolResultRuntimeMessage)
        assert result[0].tool_calls[0].name == "my_tool"

    def test_tool_result_role_alias(self) -> None:
        result = MessageMapper.to_runtime_messages([{"role": "toolResult", "content": "x"}])
        assert isinstance(result[0], ToolResultRuntimeMessage)

    def test_unknown_role_excluded_from_context(self) -> None:
        result = MessageMapper.to_runtime_messages([{"role": "unknown", "content": "x"}])
        assert isinstance(result[0], AppRuntimeMessage)
        assert result[0].include_in_context is False
        assert result[0].app_type == "provider_message"

    def test_opensre_metadata_propagated(self) -> None:
        result = MessageMapper.to_runtime_messages(
            [{"role": "user", "content": "hi", "_opensre_tag": "seed"}]
        )
        assert result[0].metadata == {"_opensre_tag": "seed"}

    def test_non_opensre_metadata_not_propagated(self) -> None:
        result = MessageMapper.to_runtime_messages(
            [{"role": "user", "content": "hi", "other_key": "val"}]
        )
        assert result[0].metadata == {}


# ---------------------------------------------------------------------------
# Pre-layer — to_provider_messages
# ---------------------------------------------------------------------------


class TestToProviderMessages:
    def test_user_message(self) -> None:
        bus = MessageMapper(_FakeLLM())
        msg = UserRuntimeMessage(content="hello")
        assert bus.to_provider_messages([msg]) == [{"role": "user", "content": "hello"}]

    def test_assistant_message_with_provider_payload_replayed(self) -> None:
        bus = MessageMapper(_FakeLLM())
        payload = {"role": "assistant", "content": "ok", "extra": 1}
        msg = AssistantRuntimeMessage(content="ok", provider_payload=payload)
        result = bus.to_provider_messages([msg])
        assert result == [payload]

    def test_assistant_message_without_payload_uses_llm(self) -> None:
        bus = MessageMapper(_FakeLLM())
        tc = ToolCall(id="t1", name="foo", input={})
        msg = AssistantRuntimeMessage(content="text", tool_calls=(tc,))
        result = bus.to_provider_messages([msg])
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "text"

    def test_tool_result_with_provider_payloads_replayed(self) -> None:
        bus = MessageMapper(_FakeLLM())
        payload = {"role": "tool", "results": [{"id": "t1", "output": "x"}]}
        tc = ToolCall(id="t1", name="foo", input={})
        msg = ToolResultRuntimeMessage(
            tool_calls=(tc,),
            results=("x",),
            provider_payloads=(payload,),
        )
        result = bus.to_provider_messages([msg])
        assert result == [payload]

    def test_normalize_then_to_provider_messages_strips_internal_markers(self) -> None:
        """A marked dict round-tripped through normalize -> to_provider_messages must not
        leak ``_opensre_*`` keys back out, even though provider_payload retains them."""
        bus = MessageMapper(_FakeLLM())
        raw = {
            "role": "assistant",
            "content": "ok",
            "_opensre_seed": True,
        }
        normalized = MessageMapper.to_runtime_messages([raw])
        result = bus.to_provider_messages(normalized)
        assert result == [{"role": "assistant", "content": "ok"}]
        assert raw["_opensre_seed"] is True

    def test_tool_result_without_payloads_builds_via_llm(self) -> None:
        bus = MessageMapper(_FakeLLM())
        tc = ToolCall(id="t1", name="foo", input={})
        msg = ToolResultRuntimeMessage(tool_calls=(tc,), results=({"ok": True},))
        result = bus.to_provider_messages([msg])
        assert result[0]["role"] == "tool"

    def test_app_message_included_in_context(self) -> None:
        bus = MessageMapper(_FakeLLM())
        msg = AppRuntimeMessage(app_type="custom", content="note", include_in_context=True)
        result = bus.to_provider_messages([msg])
        assert result == [{"role": "user", "content": "note"}]

    def test_app_message_excluded_from_context_omitted(self) -> None:
        bus = MessageMapper(_FakeLLM())
        msg = AppRuntimeMessage(app_type="custom", content="hidden", include_in_context=False)
        assert bus.to_provider_messages([msg]) == []

    def test_generic_tool_result_does_not_import_litellm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Generic/static clients must not trigger LiteLLM's cold import."""
        real_import = builtins.__import__

        def guarded(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "core.llm.transports.litellm.clients" or name.startswith("litellm"):
                raise AssertionError(f"unexpected LiteLLM import: {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", guarded)
        tc = ToolCall(id="c1", name="q", input={})
        msg = ToolResultRuntimeMessage(tool_calls=(tc,), results=({"ok": True},))
        result = MessageMapper(_FakeLLM()).to_provider_messages([msg])
        assert result[0]["role"] == "tool"


# ---------------------------------------------------------------------------
# Post-layer — to_assistant_provider_message
# ---------------------------------------------------------------------------


class TestAssistantFromResponse:
    def test_generic_llm_uses_build_assistant_message(self) -> None:
        bus = MessageMapper(_FakeLLM())
        response = AgentLLMResponse(content="done", tool_calls=[])
        result = bus.to_assistant_provider_message(response)
        assert result["role"] == "assistant"
        assert result["content"] == "done"

    def test_raw_content_returned_when_set(self) -> None:
        bus = MessageMapper(_FakeLLM())
        raw = {"role": "assistant", "content": None, "thought": "x"}
        response = AgentLLMResponse(content="done", raw_content=raw)
        assert bus.to_assistant_provider_message(response) is raw


# ---------------------------------------------------------------------------
# Post-layer — to_tool_result_provider_messages
# ---------------------------------------------------------------------------


class TestToolResultsFromExecution:
    def test_generic_llm_single_result(self) -> None:
        bus = MessageMapper(_FakeLLM())
        tc = ToolCall(id="t1", name="foo", input={})
        results = bus.to_tool_result_provider_messages([tc], [{"data": 1}])
        assert len(results) == 1
        assert results[0]["role"] == "tool"

    def test_openai_compat_returns_multiple_messages(self) -> None:
        from core.llm.transports.sdk.agent_clients import OpenAIAgentClient

        llm = OpenAIAgentClient.__new__(OpenAIAgentClient)
        bus = MessageMapper(llm)
        tc1 = ToolCall(id="t1", name="a", input={})
        tc2 = ToolCall(id="t2", name="b", input={})
        results = bus.to_tool_result_provider_messages([tc1, tc2], ["r1", "r2"])
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Post-layer — to_synthetic_assistant_provider_message
# ---------------------------------------------------------------------------


class TestSyntheticAssistantToolCall:
    def test_generic_llm_fallback_plain_text(self) -> None:
        bus = MessageMapper(_FakeLLM())
        tc = ToolCall(id="t1", name="query_logs", input={})
        result = bus.to_synthetic_assistant_provider_message([tc])
        assert result["role"] == "assistant"
        assert "query_logs" in result["content"]

    def test_anthropic_tool_use_blocks(self) -> None:
        from core.llm.transports.sdk.agent_clients import AnthropicAgentClient

        llm = AnthropicAgentClient.__new__(AnthropicAgentClient)
        bus = MessageMapper(llm)
        tc = ToolCall(id="tc1", name="get_logs", input={"q": "err"})
        result = bus.to_synthetic_assistant_provider_message([tc])
        assert result["role"] == "assistant"
        block = result["content"][0]
        assert block["type"] == "tool_use"
        assert block["id"] == "tc1"
        assert block["name"] == "get_logs"

    def test_openai_compat_function_tool_calls(self) -> None:
        from core.llm.transports.sdk.agent_clients import OpenAIAgentClient

        llm = OpenAIAgentClient.__new__(OpenAIAgentClient)
        bus = MessageMapper(llm)
        tc = ToolCall(id="tc2", name="query_k8s", input={"ns": "default"})
        result = bus.to_synthetic_assistant_provider_message([tc])
        assert result["role"] == "assistant"
        assert result["content"] is None
        fn_call = result["tool_calls"][0]
        assert fn_call["id"] == "tc2"
        assert fn_call["type"] == "function"
        args = json.loads(fn_call["function"]["arguments"])
        assert args == {"ns": "default"}

    def test_bedrock_converse_tool_use_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sys
        import types

        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setitem(
            sys.modules,
            "boto3",
            types.SimpleNamespace(
                client=lambda *_a, **_kw: types.SimpleNamespace(converse=lambda **_: {})
            ),
        )
        from core.llm.transports.sdk.agent_clients import BedrockConverseAgentClient

        llm = BedrockConverseAgentClient(model="mistral.mistral-large-3-675b-instruct")
        tc = ToolCall(id="abc12def3", name="query_logs", input={"query": "error"})
        result = MessageMapper(llm).to_synthetic_assistant_provider_message([tc])
        assert result["role"] == "assistant"
        assert result["content"][0]["toolUse"]["toolUseId"] == "abc12def3"
        assert result["content"][0]["toolUse"]["name"] == "query_logs"
        assert "I will start by querying" not in str(result)


# ---------------------------------------------------------------------------
# Dispatch — the provider adapter is resolved once, at construction
# ---------------------------------------------------------------------------


def test_a_client_that_is_no_sdk_client_never_loads_the_provider_sdks() -> None:
    """The startup menu's static ``/choose`` turn imported the OpenAI SDK before it drew."""
    check = (
        "import sys; from core.messages import MessageMapper; MessageMapper(object()); "
        "print('core.llm.transports.sdk.agent_clients' in sys.modules, 'openai' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", check], check=True, capture_output=True, text=True
    )

    assert result.stdout.split() == ["False", "False"]


class TestAdapterCaching:
    @staticmethod
    def _spy_adapter_for(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
        """Wrap ``adapter_for`` to count how many times dispatch is resolved."""
        import core.messages.message_mapper as mm

        calls = {"n": 0}
        real = mm.adapter_for

        def counting(llm: Any) -> Any:
            calls["n"] += 1
            return real(llm)

        monkeypatch.setattr(mm, "adapter_for", counting)
        return calls

    def test_adapter_resolved_once_at_construction(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._spy_adapter_for(monkeypatch)

        mapper = MessageMapper(_FakeLLM())
        assert calls["n"] == 1  # resolved eagerly at construction, not lazily per call

        # Every delegating call reuses the cached adapter — no re-dispatch.
        tc = ToolCall(id="t1", name="foo", input={})
        mapper.to_tool_result_provider_messages([tc], ["r"])
        mapper.to_synthetic_assistant_provider_message([tc])
        mapper.to_assistant_provider_message(AgentLLMResponse(content="x"))
        assert calls["n"] == 1

    def test_provider_messages_loop_does_not_redispatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._spy_adapter_for(monkeypatch)

        mapper = MessageMapper(_FakeLLM())
        # Each App message routes through the cached adapter once per message in the
        # render loop; none of these re-resolve the adapter.
        messages: list[RuntimeMessage] = [
            UserRuntimeMessage(content="hi"),
            AppRuntimeMessage(app_type="note", content="a"),
            AppRuntimeMessage(app_type="note", content="b"),
        ]
        mapper.to_provider_messages(messages)
        assert calls["n"] == 1

    def test_cached_adapter_matches_the_client(self) -> None:
        from core.llm.transports.sdk.agent_clients import AnthropicAgentClient
        from core.messages.provider_adapters import MessageAdapter, adapter_for

        mapper = MessageMapper(AnthropicAgentClient.__new__(AnthropicAgentClient))
        assert isinstance(mapper._adapter, MessageAdapter)
        # The cached adapter is the same kind adapter_for would resolve for this client.
        assert type(mapper._adapter) is type(adapter_for(mapper._llm))
