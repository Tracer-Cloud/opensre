"""Unit tests for configuration helper utilities."""

from __future__ import annotations

from app.utils.cfg_helpers import CfgHelpers


def test_get_clean_env_value_strips_whitespace(monkeypatch) -> None:
    """
    Get a clean environment value from the env var.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.setenv("TEST_ENV_KEY", "  some-value  ")

    assert CfgHelpers.get_clean_env_value("TEST_ENV_KEY") == "some-value"


def test_get_clean_env_value_returns_empty_for_missing_key(monkeypatch) -> None:
    """
    Get empty string when the env key is missing.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.delenv("MISSING_ENV_KEY", raising=False)

    assert CfgHelpers.get_clean_env_value("MISSING_ENV_KEY") == ""


def test_first_env_or_default_prefers_first_non_empty_key(monkeypatch) -> None:
    """
    Resolve first non-empty key value by order.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.setenv("FIRST_KEY", "   ")
    monkeypatch.setenv("SECOND_KEY", "chosen")
    monkeypatch.setenv("THIRD_KEY", "ignored")

    resolved = CfgHelpers.first_env_or_default(
        env_keys=("FIRST_KEY", "SECOND_KEY", "THIRD_KEY"),
        default="fallback",
    )

    assert resolved == "chosen"


def test_first_env_or_default_returns_default_when_all_empty(monkeypatch) -> None:
    """
    Fall back to default when all candidate values are empty.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.setenv("FIRST_KEY", "")
    monkeypatch.delenv("SECOND_KEY", raising=False)

    resolved = CfgHelpers.first_env_or_default(
        env_keys=("FIRST_KEY", "SECOND_KEY"),
        default="fallback",
    )

    assert resolved == "fallback"


def test_first_env_or_default_matches_openai_tool_fallback_order(monkeypatch) -> None:
    """
    Match OpenAI tool model fallback precedence.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.delenv("OPENAI_TOOLCALL_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "reasoning-fallback")
    monkeypatch.setenv("OPENAI_MODEL", "generic-fallback")

    resolved = CfgHelpers.first_env_or_default(
        env_keys=("OPENAI_TOOLCALL_MODEL", "OPENAI_REASONING_MODEL", "OPENAI_MODEL"),
        default="default-tool",
    )

    assert resolved == "reasoning-fallback"


def test_resolve_llm_provider_defaults_to_anthropic(monkeypatch) -> None:
    """
    Resolve anthropic as default provider when env var is absent.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    assert CfgHelpers.resolve_llm_provider() == "anthropic"


def test_resolve_llm_provider_is_trimmed_and_lowercased(monkeypatch) -> None:
    """
    Normalize provider by trimming and lowercasing the env value.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.setenv("LLM_PROVIDER", "  OpenAI  ")

    assert CfgHelpers.resolve_llm_provider() == "openai"
