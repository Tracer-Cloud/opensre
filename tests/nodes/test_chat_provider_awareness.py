"""Provider-awareness tests for chat node LLM initialization."""

from __future__ import annotations

import pytest

from app.nodes import chat


class _DummyBoundModel:
    """
    Hold provider metadata for bound test model instances.
    """

    def __init__(self, provider: str, model: str) -> None:
        """
        Initialize dummy bound model.

        Args:
            provider: Provider name set during model creation.
            model: Model name set during model creation.

        Returns:
            None.
        """
        self.provider = provider
        self.model = model

    def invoke(self, _messages):
        """
        Return dummy invoke result for test model calls.

        Args:
            _messages: Messages passed to the model invoke call.

        Returns:
            None.
        """
        return None


class _DummyChatModel:
    """
    Provide a fake chat model with bind_tools support.
    """

    def __init__(self, provider: str, model: str, **_kwargs) -> None:
        """
        Initialize dummy chat model.

        Args:
            provider: Provider name set during model creation.
            model: Model name set during model creation.
            _kwargs: Extra constructor kwargs accepted by real chat models.

        Returns:
            None.
        """
        self.provider = provider
        self.model = model

    def bind_tools(self, _tools):
        """
        Return a dummy tool-bound model carrying provider metadata.

        Args:
            _tools: Tool definitions passed during model binding.

        Returns:
            _DummyBoundModel: Bound model for subsequent invoke calls.
        """
        return _DummyBoundModel(provider=self.provider, model=self.model)


class _FakeOpenAIModule:
    """
    Mimic the OpenAI provider module for import patching.
    """

    @staticmethod
    def ChatOpenAI(**kwargs):
        """
        Construct fake OpenAI chat model used by import patching.

        Args:
            **kwargs: Constructor args forwarded to the dummy model.

        Returns:
            _DummyChatModel: Provider-tagged fake chat model.
        """
        return _DummyChatModel(provider="openai", **kwargs)


class _FakeAnthropicModule:
    """
    Mimic the Anthropic provider module for import patching.

    Returns:
        None.
    """

    @staticmethod
    def ChatAnthropic(**kwargs):
        """
        Construct fake Anthropic chat model used by import patching.

        Args:
            **kwargs: Constructor args forwarded to the dummy model.

        Returns:
            _DummyChatModel: Provider-tagged fake chat model.
        """
        return _DummyChatModel(provider="anthropic", **kwargs)


def _patch_llm_imports(monkeypatch) -> None:
    """
    Patch dynamic provider imports with local fake modules.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """

    def _fake_import_module(module_name: str):
        if module_name == "langchain_openai":
            return _FakeOpenAIModule
        if module_name == "langchain_anthropic":
            return _FakeAnthropicModule
        raise AssertionError(f"Unexpected module import: {module_name}")

    monkeypatch.setattr(chat, "import_module", _fake_import_module)


def _reset_chat_cache(monkeypatch) -> None:
    """
    Reset cached chat model singletons between test runs.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.setattr(chat, "_chat_llm_cache", {})
    monkeypatch.setattr(chat, "_chat_llm_with_tools_cache", {})


def test_get_chat_llm_uses_openai_toolcall_model_when_provider_openai(
    monkeypatch,
) -> None:
    """
    Build OpenAI tool-bound model when provider is openai.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "gpt-openai-tools")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-openai-reasoning")
    monkeypatch.delenv("ANTHROPIC_TOOLCALL_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_REASONING_MODEL", raising=False)

    _patch_llm_imports(monkeypatch)
    _reset_chat_cache(monkeypatch)

    llm = chat._get_chat_llm(with_tools=True)
    assert getattr(llm, "provider", "") == "openai"
    assert getattr(llm, "model", "") == "gpt-openai-tools"


def test_get_chat_llm_uses_openai_reasoning_model_when_without_tools(
    monkeypatch,
) -> None:
    """
    Build OpenAI reasoning model when tools are disabled.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-openai-reasoning")
    monkeypatch.delenv("OPENAI_TOOLCALL_MODEL", raising=False)

    _patch_llm_imports(monkeypatch)
    _reset_chat_cache(monkeypatch)

    llm = chat._get_chat_llm(with_tools=False)
    assert getattr(llm, "provider", "") == "openai"
    assert getattr(llm, "model", "") == "gpt-openai-reasoning"


def test_get_chat_llm_uses_anthropic_models(monkeypatch) -> None:
    """
    Build Anthropic tool and reasoning models from env values.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_TOOLCALL_MODEL", "claude-tools")
    monkeypatch.setenv("ANTHROPIC_REASONING_MODEL", "claude-reason")

    _patch_llm_imports(monkeypatch)
    _reset_chat_cache(monkeypatch)

    tool_llm = chat._get_chat_llm(with_tools=True)
    reasoning_llm = chat._get_chat_llm(with_tools=False)

    assert getattr(tool_llm, "provider", "") == "anthropic"
    assert getattr(tool_llm, "model", "") == "claude-tools"
    assert getattr(reasoning_llm, "provider", "") == "anthropic"
    assert getattr(reasoning_llm, "model", "") == "claude-reason"


def test_get_chat_llm_rebuilds_when_provider_changes(monkeypatch) -> None:
    """
    Rebuild cached tool model when provider changes across calls.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "gpt-openai-tools")
    monkeypatch.setenv("ANTHROPIC_TOOLCALL_MODEL", "claude-tools")

    _patch_llm_imports(monkeypatch)
    _reset_chat_cache(monkeypatch)

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    llm_openai = chat._get_chat_llm(with_tools=True)

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    llm_anthropic = chat._get_chat_llm(with_tools=True)

    assert getattr(llm_openai, "provider", "") == "openai"
    assert getattr(llm_anthropic, "provider", "") == "anthropic"


def test_get_chat_llm_raises_for_unsupported_provider(monkeypatch) -> None:
    """
    Raise ValueError when provider value is unsupported.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.setenv("LLM_PROVIDER", "unsupported")
    _reset_chat_cache(monkeypatch)

    with pytest.raises(ValueError):
        chat._get_chat_llm(with_tools=True)
