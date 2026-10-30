from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import litellm
import pytest

from config.llm_models import PROVIDER_MODEL_DEFAULTS
from core.llm.transports.litellm.routing import build_litellm_agent_client, build_litellm_llm_client

PROVIDERS = tuple(name for name in PROVIDER_MODEL_DEFAULTS if name != "custom-anthropic")


def _settings() -> SimpleNamespace:
    fields: dict[str, Any] = {
        "azure_openai_base_url": "https://resource.test",
        "azure_openai_api_version": "2024-10-21",
        "vertex_ai_project": "project",
        "vertex_ai_location": "eu",
        "ollama_host": "http://localhost:11434",
        "ollama_model": "alias",
        "custom_openai_base_url": "https://gateway.test/v1",
    }
    for defaults in PROVIDER_MODEL_DEFAULTS.values():
        for role in ("reasoning", "classification", "toolcall"):
            fields[f"{defaults.settings_key}_{role}_model"] = "alias"
    return SimpleNamespace(**fields)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_agent_preserves_raw_tool_metadata_on_replay(provider: str) -> None:
    requests: list[dict[str, Any]] = []
    raw = {
        "role": "assistant",
        "content": "",
        "reasoning_content": "opaque reasoning",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
                "provider_specific_fields": {"thought_signature": "opaque signature"},
            }
        ],
    }

    def completion(**kwargs: Any) -> Any:
        requests.append(kwargs)
        return litellm.ModelResponse(
            choices=[{"index": 0, "message": raw, "finish_reason": "tool_calls"}]
        )

    client = build_litellm_agent_client(_settings(), provider)
    client._credential_resolver = lambda _name: "test-key"
    client._completion_func = completion
    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }
    ]
    messages = [{"role": "user", "content": "hello", "_opensre_seed": True}]
    reply = client.invoke(messages, tools=tools)
    client.invoke(
        [
            *messages,
            reply.raw_content,
            *client.build_tool_result_messages(reply.tool_calls, [{"ok": True}]),
        ],
        tools=tools,
    )
    assert len(requests) == 2
    replay = requests[1]["messages"][1]
    assert (
        replay["tool_calls"][0]["provider_specific_fields"]["thought_signature"]
        == "opaque signature"
    )
    assert replay["reasoning_content"] == "opaque reasoning"
    assert requests[1]["tools"] == tools
    assert requests[1]["messages"][-1]["tool_call_id"] == "call-1"
    assert "_opensre_seed" not in requests[0]["messages"][0]
    assert messages[0]["_opensre_seed"] is True
    if provider in {"bedrock", "vertex-ai"}:
        assert "api_key" not in requests[0]
    if provider == "vertex-ai":
        assert requests[0]["vertex_project"] == "project"
        assert requests[0]["vertex_location"] == "eu"
    if provider == "azure-openai":
        assert requests[0]["api_version"] == "2024-10-21"


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("role", ["reasoning", "classification", "toolcall"])
def test_each_provider_role_builds_a_request_without_discovery(provider: str, role: str) -> None:
    requests: list[dict[str, Any]] = []

    def completion(**kwargs: Any) -> Any:
        requests.append(kwargs)
        return {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }

    client = build_litellm_llm_client(_settings(), provider, role)
    client._credential_resolver = lambda _name: "test-key"
    client._completion_func = completion
    reply = client.invoke("hello")
    assert reply.content == "ok"
    assert (reply.input_tokens, reply.output_tokens) == (3, 2)
    assert len(requests) == 1
    assert requests[0]["model"].endswith("/alias")
    assert requests[0]["messages"] == [{"role": "user", "content": "hello"}]
