from __future__ import annotations

import json
from functools import partial
from http import HTTPStatus
from typing import Any

import httpx
import litellm
import openai
import pytest

from config.llm_settings import LLMSettings
from core.llm.transports.litellm import clients
from core.llm.transports.litellm.routing import build_litellm_agent_client, build_litellm_llm_client
from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE


@pytest.mark.parametrize("provider", ["gemini", "groq", "vertex-ai"])
def test_current_role_defaults_are_selectable(provider: str) -> None:
    settings = LLMSettings.model_validate({"provider": provider})
    choices = {row.value for row in PROVIDER_BY_VALUE[provider].models}
    prefix = provider.replace("-", "_")
    for role in ("reasoning", "classification", "toolcall"):
        assert getattr(settings, f"{prefix}_{role}_model") in choices


def test_explicit_legacy_models_and_vertex_location_are_preserved() -> None:
    settings = LLMSettings.model_validate(
        {
            "provider": "vertex-ai",
            "vertex_ai_reasoning_model": "gemini-2.5-pro",
            "vertex_ai_location": "us-central1",
            "groq_reasoning_model": "llama-3.3-70b-versatile",
        }
    )
    vertex = build_litellm_agent_client(settings, "vertex-ai")
    groq = build_litellm_agent_client(settings, "groq")
    assert vertex._litellm_model == "vertex_ai/gemini-2.5-pro"
    assert vertex._vertex_location == "us-central1"
    assert groq._litellm_model == "openai/llama-3.3-70b-versatile"


@pytest.mark.parametrize(
    "provider,model",
    [
        ("groq", "openai/gpt-oss-120b"),
        ("groq", "openai/gpt-oss-20b"),
        ("openrouter", "openai/gpt-5.4-mini"),
    ],
)
@pytest.mark.parametrize("agent", [True, False])
def test_compatible_endpoint_receives_complete_namespaced_model_id(
    monkeypatch: pytest.MonkeyPatch, provider: str, model: str, agent: bool
) -> None:
    bodies: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(
            HTTPStatus.OK,
            json={
                "id": "test",
                "created": 0,
                "model": model,
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    settings = LLMSettings.model_validate(
        {
            "provider": provider,
            f"{provider}_reasoning_model": model,
            f"{provider}_toolcall_model": model,
        }
    )
    monkeypatch.setenv(f"{provider.upper()}_API_KEY", "test-key")
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        sdk = openai.OpenAI(
            api_key="test-key", base_url="https://example.test/v1", http_client=http, max_retries=0
        )
        monkeypatch.setattr(clients, "completion", partial(litellm.completion, client=sdk))
        if agent:
            result = build_litellm_agent_client(settings, provider).invoke(
                [{"role": "user", "content": "hi"}]
            )
        else:
            result = build_litellm_llm_client(settings, provider, "toolcall").invoke("hi")
    assert result.content == "ok"
    assert len(bodies) == 1
    assert bodies[0]["model"] == model
