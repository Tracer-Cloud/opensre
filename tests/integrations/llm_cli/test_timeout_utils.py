from __future__ import annotations

from unittest.mock import patch

import pytest

from integrations.llm_cli.antigravity_cli import AntigravityCLIAdapter
from integrations.llm_cli.claude_code import ClaudeCodeAdapter
from integrations.llm_cli.gemini_cli import GeminiCLIAdapter
from integrations.llm_cli.grok_cli import GrokCLIAdapter
from integrations.llm_cli.timeout_utils import resolve_timeout_from_env


def test_resolve_timeout_from_env_defaults_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("X_TIMEOUT", raising=False)
    assert (
        resolve_timeout_from_env(
            env_key="X_TIMEOUT",
            default=120.0,
            minimum=30.0,
            maximum=600.0,
        )
        == 120.0
    )


def test_resolve_timeout_from_env_defaults_when_invalid(monkeypatch) -> None:
    monkeypatch.setenv("X_TIMEOUT", "oops")
    assert (
        resolve_timeout_from_env(
            env_key="X_TIMEOUT",
            default=120.0,
            minimum=30.0,
            maximum=600.0,
        )
        == 120.0
    )


def test_resolve_timeout_from_env_defaults_when_non_positive(monkeypatch) -> None:
    monkeypatch.setenv("X_TIMEOUT", "0")
    assert (
        resolve_timeout_from_env(
            env_key="X_TIMEOUT",
            default=120.0,
            minimum=30.0,
            maximum=600.0,
        )
        == 120.0
    )


def test_resolve_timeout_from_env_clamps(monkeypatch) -> None:
    monkeypatch.setenv("X_TIMEOUT", "1")
    assert (
        resolve_timeout_from_env(
            env_key="X_TIMEOUT",
            default=120.0,
            minimum=30.0,
            maximum=600.0,
        )
        == 30.0
    )

    monkeypatch.setenv("X_TIMEOUT", "9999")
    assert (
        resolve_timeout_from_env(
            env_key="X_TIMEOUT",
            default=120.0,
            minimum=30.0,
            maximum=600.0,
        )
        == 600.0
    )


def test_resolve_timeout_from_env_uses_value(monkeypatch) -> None:
    monkeypatch.setenv("X_TIMEOUT", "180")
    assert (
        resolve_timeout_from_env(
            env_key="X_TIMEOUT",
            default=120.0,
            minimum=30.0,
            maximum=600.0,
        )
        == 180.0
    )


@pytest.mark.parametrize(
    ("adapter_cls", "timeout_env", "bin_env", "subprocess_buffer"),
    [
        (AntigravityCLIAdapter, "ANTIGRAVITY_CLI_TIMEOUT_SECONDS", "ANTIGRAVITY_CLI_BIN", 10.0),
        (ClaudeCodeAdapter, "CLAUDE_CODE_TIMEOUT_SECONDS", "CLAUDE_CODE_BIN", 0.0),
        (GeminiCLIAdapter, "GEMINI_CLI_TIMEOUT_SECONDS", "GEMINI_CLI_BIN", 0.0),
        (GrokCLIAdapter, "GROK_CLI_TIMEOUT_SECONDS", "GROK_CLI_BIN", 0.0),
    ],
)
@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 300.0), ("oops", 300.0), ("0", 300.0), ("5", 30.0), ("9999", 600.0), ("240", 240.0)],
)
def test_adapter_build_resolves_exec_timeout_from_env(
    monkeypatch: pytest.MonkeyPatch,
    adapter_cls: type,
    timeout_env: str,
    bin_env: str,
    subprocess_buffer: float,
    raw: str | None,
    expected: float,
) -> None:
    monkeypatch.delenv(bin_env, raising=False)
    if raw is None:
        monkeypatch.delenv(timeout_env, raising=False)
    else:
        monkeypatch.setenv(timeout_env, raw)

    with patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/cli"):
        invocation = adapter_cls().build(prompt="p", model=None, workspace="")

    assert invocation.timeout_sec == expected + subprocess_buffer
