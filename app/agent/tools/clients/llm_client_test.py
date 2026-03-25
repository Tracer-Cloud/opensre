from __future__ import annotations

from app.agent.tools.clients import llm_client
from app.agent.tools.clients.llm_client import _format_anthropic_retry_error


def test_format_anthropic_retry_error_for_connection_issue() -> None:
    APIConnectionError = type("APIConnectionError", (Exception,), {})

    message = _format_anthropic_retry_error(APIConnectionError("boom"))

    assert "connection failed" in message.lower()


def test_get_llm_uses_openai_model_from_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5-mini")
    monkeypatch.setattr(llm_client, "_llm", None)

    client = llm_client.get_llm()

    assert isinstance(client, llm_client.OpenAILLMClient)
    assert client._model == "gpt-5-mini"
    monkeypatch.setattr(llm_client, "_llm", None)


def test_format_anthropic_retry_error_for_529() -> None:
    OverloadedError = type("OverloadedError", (Exception,), {"status_code": 529})

    message = _format_anthropic_retry_error(OverloadedError("busy"))

    assert "HTTP 529" in message
