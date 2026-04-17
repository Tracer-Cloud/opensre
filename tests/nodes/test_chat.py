"""Tests for chat branch LangChain LLM selection (LLM_PROVIDER)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.nodes import chat as chat_mod


def _clear_chat_llm_singletons() -> None:
    """Reset module-level chat model cache (isolated test runs)."""
    chat_mod._chat_llm_cache.clear()
    chat_mod._chat_llm_with_tools_cache.clear()


class _OpenAIModule:
    def __init__(self, chat_openai: MagicMock) -> None:
        self.ChatOpenAI = chat_openai


class _AnthropicModule:
    def __init__(self, chat_anthropic: MagicMock) -> None:
        self.ChatAnthropic = chat_anthropic


@pytest.fixture
def openai_chat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


@pytest.mark.usefixtures("openai_chat_env")
def test_get_chat_llm_openai_with_tools_uses_chat_openai() -> None:
    _clear_chat_llm_singletons()
    with patch.object(chat_mod, "import_module") as mock_import_module:
        mock_base = MagicMock()
        mock_bound = MagicMock()
        mock_base.bind_tools.return_value = mock_bound
        mock_openai = MagicMock(return_value=mock_base)
        mock_import_module.return_value = _OpenAIModule(mock_openai)
        out = chat_mod._get_chat_llm(with_tools=True)
        mock_openai.assert_called_once()
        assert out is mock_bound


@pytest.mark.usefixtures("openai_chat_env")
def test_get_chat_llm_openai_without_tools_uses_chat_openai() -> None:
    _clear_chat_llm_singletons()
    with patch.object(chat_mod, "import_module") as mock_import_module:
        mock_llm = MagicMock()
        mock_openai = MagicMock(return_value=mock_llm)
        mock_import_module.return_value = _OpenAIModule(mock_openai)
        out = chat_mod._get_chat_llm(with_tools=False)
        mock_openai.assert_called_once()
        assert out is mock_llm


def test_get_chat_llm_anthropic_uses_chat_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    _clear_chat_llm_singletons()
    with patch.object(chat_mod, "import_module") as mock_import_module:
        mock_llm = MagicMock()
        mock_anthropic = MagicMock(return_value=mock_llm)
        mock_import_module.return_value = _AnthropicModule(mock_anthropic)
        out = chat_mod._get_chat_llm(with_tools=False)
        mock_anthropic.assert_called_once()
        assert out is mock_llm
