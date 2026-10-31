"""Google Vertex AI LiteLLM routing tests."""

from __future__ import annotations

from types import SimpleNamespace

from core.llm.transport_mode import use_litellm_for_provider
from core.llm.transports.litellm.clients import LiteLLMAgentClient, LiteLLMLLMClient
from core.llm.transports.litellm.routing import build_litellm_agent_client, build_litellm_llm_client


def _vertex_settings() -> SimpleNamespace:
    return SimpleNamespace(
        provider="vertex-ai",
        vertex_ai_project="my-gcp-project",
        vertex_ai_location="us-central1",
        vertex_ai_reasoning_model="gemini-2.5-pro",
        vertex_ai_classification_model="gemini-2.5-flash",
        vertex_ai_toolcall_model="gemini-2.5-flash-lite",
    )


def test_use_litellm_for_provider_is_always_true_for_vertex_ai() -> None:
    assert use_litellm_for_provider("vertex-ai") is True


def test_build_litellm_agent_client_for_vertex_ai() -> None:
    client = build_litellm_agent_client(_vertex_settings(), "vertex-ai")

    assert isinstance(client, LiteLLMAgentClient)
    assert client._litellm_model == "vertex_ai/gemini-2.5-pro"
    assert client._vertex_project == "my-gcp-project"
    assert client._vertex_location == "us-central1"
    assert client._api_key_env is None


def test_build_litellm_llm_client_for_vertex_ai() -> None:
    client = build_litellm_llm_client(
        _vertex_settings(),
        "vertex-ai",
        "reasoning",
    )

    assert isinstance(client, LiteLLMLLMClient)
    assert client._litellm_model == "vertex_ai/gemini-2.5-pro"
    assert client._vertex_project == "my-gcp-project"
    assert client._vertex_location == "us-central1"
    assert client._model_fallback == "vertex_ai/gemini-2.5-flash-lite"
    assert client._api_key_env is None


def test_vertex_ai_defaults_location_when_unset() -> None:
    settings = _vertex_settings()
    settings.vertex_ai_location = ""

    client = build_litellm_agent_client(settings, "vertex-ai")

    assert client._vertex_location == "us"


def test_vertex_ai_omits_project_when_unset_instead_of_raising() -> None:
    """Bedrock parity: a missing project is not an OpenSRE-side precondition —
    it surfaces as a LiteLLM/google-auth error at request time instead."""
    settings = _vertex_settings()
    settings.vertex_ai_project = ""

    client = build_litellm_agent_client(settings, "vertex-ai")

    assert client._vertex_project is None
