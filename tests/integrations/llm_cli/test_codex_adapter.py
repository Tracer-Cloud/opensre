"""Tests for Codex CLI adapter detection and prompt helpers."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from integrations.llm_cli.binary_resolver import diagnose_binary_path, npm_prefix_bin_dirs
from integrations.llm_cli.codex import (
    _AUTH_STATUS_PROBE_ENV,
    CodexAdapter,
    _fallback_codex_paths,
)
from integrations.llm_cli.text import flatten_messages_to_prompt
from tests.integrations.llm_cli.testing_helpers import write_fake_runnable_cli_bin


@pytest.fixture(autouse=True)
def _enable_codex_auth_status_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_AUTH_STATUS_PROBE_ENV, "1")


def _posix_path_set(paths: list[str]) -> set[str]:
    """Normalize paths for assertions when simulating POSIX platforms on Windows CI."""
    return {Path(p).as_posix() for p in paths}


def test_flatten_messages_joins_roles() -> None:
    text = flatten_messages_to_prompt(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert "=== SYSTEM ===" in text
    assert "sys" in text
    assert "=== USER ===" in text
    assert "hi" in text


def _version_proc() -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = "codex-cli 0.120.0\n"
    m.stderr = ""
    return m


def _login_ok_proc() -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = "Logged in using ChatGPT\n"
    m.stderr = ""
    return m


@patch("integrations.llm_cli.codex.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_path_binary_logged_in(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/codex"

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if len(args) >= 2 and args[1] == "--version":
            return _version_proc()
        if len(args) >= 3 and args[1] == "login" and args[2] == "status":
            return _login_ok_proc()
        raise AssertionError(args)

    mock_run.side_effect = side_effect
    probe = CodexAdapter().detect()
    assert probe.installed is True
    assert probe.logged_in is True
    assert probe.bin_path == "/usr/bin/codex"
    assert probe.version == "0.120.0"


@patch("integrations.llm_cli.codex.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_not_logged_in(
    mock_which: MagicMock, mock_run: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Strip OPENAI_API_KEY so the dev's shell or .env can't trigger
    # the API-key fallback and flip logged_in to True.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    mock_which.return_value = "/usr/bin/codex"

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if len(args) >= 2 and args[1] == "--version":
            return _version_proc()
        if len(args) >= 3 and args[1] == "login":
            m = MagicMock()
            m.returncode = 1
            m.stdout = ""
            m.stderr = "Not logged in\n"
            return m
        raise AssertionError(args)

    mock_run.side_effect = side_effect
    probe = CodexAdapter().detect()
    assert probe.installed is True
    assert probe.logged_in is False


@patch("integrations.llm_cli.codex.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_skips_login_status_probe_by_default(
    mock_which: MagicMock, mock_run: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(_AUTH_STATUS_PROBE_ENV, raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    mock_which.return_value = "/usr/bin/codex"

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if len(args) >= 2 and args[1] == "--version":
            return _version_proc()
        raise AssertionError(f"unexpected auth probe: {args}")

    mock_run.side_effect = side_effect
    probe = CodexAdapter().detect()
    assert probe.installed is True
    assert probe.logged_in is None
    assert probe.detail == "Codex CLI installed."
    assert mock_run.call_count == 1


@patch("integrations.llm_cli.codex.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_not_logged_in_uses_openai_api_key_fallback(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    mock_which.return_value = "/usr/bin/codex"

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if len(args) >= 2 and args[1] == "--version":
            return _version_proc()
        if len(args) >= 3 and args[1] == "login":
            m = MagicMock()
            m.returncode = 1
            m.stdout = ""
            m.stderr = "Not logged in\n"
            return m
        raise AssertionError(args)

    mock_run.side_effect = side_effect
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-fallback"}, clear=False):
        probe = CodexAdapter().detect()
    assert probe.installed is True
    assert probe.logged_in is True
    assert "OPENAI_API_KEY fallback" in probe.detail


@patch("integrations.llm_cli.codex.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_not_logged_in_exit_zero(
    mock_which: MagicMock, mock_run: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Some Codex versions may exit 0 while printing 'Not logged in' — must not match 'logged in'."""
    # Strip OPENAI_API_KEY so the dev's shell or .env can't trigger
    # the API-key fallback and flip logged_in to True.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    mock_which.return_value = "/usr/bin/codex"

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if len(args) >= 2 and args[1] == "--version":
            return _version_proc()
        if len(args) >= 3 and args[1] == "login":
            m = MagicMock()
            m.returncode = 0
            m.stdout = "Not logged in\n"
            m.stderr = ""
            return m
        raise AssertionError(args)

    mock_run.side_effect = side_effect
    probe = CodexAdapter().detect()
    assert probe.installed is True
    assert probe.logged_in is False


@patch("integrations.llm_cli.codex.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_unclear_auth_uses_openai_api_key_fallback(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    mock_which.return_value = "/usr/bin/codex"

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if len(args) >= 2 and args[1] == "--version":
            return _version_proc()
        if len(args) >= 3 and args[1] == "login":
            m = MagicMock()
            m.returncode = 2
            m.stdout = ""
            m.stderr = "network unreachable"
            return m
        raise AssertionError(args)

    mock_run.side_effect = side_effect
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-fallback"}, clear=False):
        probe = CodexAdapter().detect()
    assert probe.installed is True
    assert probe.logged_in is True
    assert "OPENAI_API_KEY fallback" in probe.detail


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/codex")
def test_build_forwards_openai_platform_env(mock_which: MagicMock) -> None:
    with patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_ORG_ID": "org-x",
            "OPENAI_PROJECT_ID": "proj-y",
            "OPENAI_BASE_URL": "https://example.invalid/v1",
        },
        clear=False,
    ):
        inv = CodexAdapter().build(prompt="p", model=None, workspace="")

    mock_which.assert_called()
    assert inv.env is not None
    assert inv.env["OPENAI_API_KEY"] == "sk-test"
    assert inv.env["OPENAI_ORG_ID"] == "org-x"
    assert inv.env["OPENAI_PROJECT_ID"] == "proj-y"
    assert inv.env["OPENAI_BASE_URL"] == "https://example.invalid/v1"


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/codex")
def test_build_omits_env_without_openai_platform_vars(mock_which: MagicMock) -> None:
    """Strip OPENAI_* so build() does not attach env overrides from the real shell."""
    base = {k: v for k, v in os.environ.items() if not k.startswith("OPENAI_")}
    with patch.dict(os.environ, base, clear=True):
        inv = CodexAdapter().build(prompt="p", model=None, workspace="")
    mock_which.assert_called()
    assert inv.env is None


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/codex")
def test_build_uses_the_hosted_account_route_when_no_openai_key_is_set(
    mock_which: MagicMock,
) -> None:
    """On a hosted gateway the account token is Codex's key and the webapp its base URL."""
    from config.account import AccountLLMRoute

    base = {k: v for k, v in os.environ.items() if not k.startswith("OPENAI_")}
    route = AccountLLMRoute(base_url="https://app.example/api/llm/v1", model="gpt-5.6-sol")
    with (
        patch.dict(os.environ, base, clear=True),
        patch("config.account.account_llm_route", return_value=route),
        patch("integrations.llm_cli.codex.account_llm_route", return_value=route),
        patch("config.account.resolve_account_token", return_value="osre_gw_org.tok"),
    ):
        inv = CodexAdapter().build(prompt="p", model=None, workspace="")

    mock_which.assert_called()
    assert inv.env == {
        "OPENAI_API_KEY": "osre_gw_org.tok",
        "OPENAI_BASE_URL": "https://app.example/api/llm/v1",
    }
    idx = inv.argv.index("-m")
    assert inv.argv[idx + 1] == "gpt-5.6-sol"
    # Codex ignores OPENAI_BASE_URL for its built-in provider: a named provider is passed.
    assert "model_provider=opensre" in inv.argv
    assert 'model_providers.opensre.base_url="https://app.example/api/llm/v1"' in inv.argv
    assert 'model_providers.opensre.env_key="OPENAI_API_KEY"' in inv.argv


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/codex")
def test_build_never_pairs_the_account_token_with_another_base_url(mock_which: MagicMock) -> None:
    """A base URL of the user's own, without a key, gets no account token and no hosted model."""
    from config.account import AccountLLMRoute

    base = {k: v for k, v in os.environ.items() if not k.startswith("OPENAI_")}
    base["OPENAI_BASE_URL"] = "https://proxy.example/v1"
    route = AccountLLMRoute(base_url="https://app.example/api/llm/v1", model="gpt-5.6-sol")
    with (
        patch.dict(os.environ, base, clear=True),
        patch("config.account.account_llm_route", return_value=route),
        patch("integrations.llm_cli.codex.account_llm_route", return_value=route),
        patch("config.account.resolve_account_token", return_value="osre_gw_org.tok"),
    ):
        inv = CodexAdapter().build(prompt="p", model=None, workspace="")

    mock_which.assert_called()
    assert inv.env == {"OPENAI_BASE_URL": "https://proxy.example/v1"}
    assert "-m" not in inv.argv


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/codex")
def test_build_prefers_explicit_openai_env_over_the_hosted_route(mock_which: MagicMock) -> None:
    from config.account import AccountLLMRoute

    route = AccountLLMRoute(base_url="https://app.example/api/llm/v1", model="gpt-5.6-sol")
    with (
        patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False),
        patch("integrations.llm_cli.codex.account_llm_route", return_value=route),
    ):
        inv = CodexAdapter().build(prompt="p", model=None, workspace="")

    mock_which.assert_called()
    assert inv.env is not None and inv.env["OPENAI_API_KEY"] == "sk-test"
    assert "OPENAI_BASE_URL" not in inv.env
    assert "-m" not in inv.argv


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/codex")
def test_build_adds_model_flag_when_not_default(mock_which: MagicMock) -> None:
    inv = CodexAdapter().build(prompt="p", model="o3", workspace="")
    assert inv.stdin == "p"
    assert "-m" in inv.argv
    assert inv.argv[-1] == "-"
    idx = inv.argv.index("-m")
    assert inv.argv[idx + 1] == "o3"
    mock_which.assert_called()


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/codex")
def test_build_adds_reasoning_effort_override(mock_which: MagicMock) -> None:
    inv = CodexAdapter().build(prompt="p", model=None, workspace="", reasoning_effort="xhigh")

    assert "-c" in inv.argv
    idx = inv.argv.index("-c")
    assert inv.argv[idx + 1] == 'model_reasoning_effort="xhigh"'
    mock_which.assert_called()


@patch("integrations.llm_cli.runner.subprocess.run")
def test_cli_backed_client_invoke(mock_run: MagicMock) -> None:
    from integrations.llm_cli.runner import CLIBackedLLMClient

    mock_adapter = MagicMock()
    mock_adapter.name = "codex"
    mock_adapter.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/codex",
        logged_in=True,
        detail="ok",
    )
    mock_adapter.build.return_value = MagicMock(
        argv=["/usr/bin/codex", "exec", "-"],
        stdin="hello",
        cwd="/tmp",
        env={"CODEX_BIN": "/custom/codex"},
        timeout_sec=30.0,
    )
    mock_adapter.parse.return_value = "answer"
    mock_adapter.explain_failure.return_value = "fail"

    mock_run.return_value = MagicMock(returncode=0, stdout="answer\n", stderr="")

    with (
        patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr,
        patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "anthropic-secret",
                "OPENAI_API_KEY": "openai-secret",
                "PATH": "/usr/bin",
            },
            clear=False,
        ),
    ):
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter, model="codex", max_tokens=256)
        resp = client.invoke("hello")

    assert resp.content == "answer"
    mock_adapter.build.assert_called_once()
    mock_run.assert_called_once()
    env = mock_run.call_args.kwargs["env"]
    assert env["PATH"] == "/usr/bin"
    assert env["CODEX_BIN"] == "/custom/codex"
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env


@patch("integrations.llm_cli.runner.subprocess.run")
def test_cli_backed_client_passes_reasoning_effort_to_adapter(mock_run: MagicMock) -> None:
    from integrations.llm_cli.runner import CLIBackedLLMClient

    mock_adapter = MagicMock()
    mock_adapter.name = "codex"
    mock_adapter.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/codex",
        logged_in=True,
        detail="ok",
    )
    mock_adapter.build.return_value = MagicMock(
        argv=["/usr/bin/codex", "exec", "-"],
        stdin="hello",
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.parse.return_value = "answer"
    mock_adapter.explain_failure.return_value = "fail"
    mock_run.return_value = MagicMock(returncode=0, stdout="answer\n", stderr="")

    with (
        patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr,
        patch.dict(os.environ, {"OPENSRE_REASONING_EFFORT": "high"}, clear=False),
    ):
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter, model="codex", max_tokens=256)
        client.invoke("hello")

    mock_adapter.build.assert_called_once_with(
        prompt="hello",
        model="codex",
        workspace="",
        reasoning_effort="high",
    )


@patch("integrations.llm_cli.runner.subprocess.run")
@patch.object(CodexAdapter, "_probe_binary")
@patch.object(CodexAdapter, "_resolve_binary", return_value="/usr/bin/codex")
def test_cli_backed_client_codex_merge_openai_platform_env(
    _mock_resolve: MagicMock,
    mock_probe_binary: MagicMock,
    mock_run: MagicMock,
) -> None:
    """Codex invoke merges OPENAI_* from the adapter into the filtered subprocess env."""
    from integrations.llm_cli.runner import CLIBackedLLMClient

    mock_probe_binary.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/codex",
        logged_in=True,
        detail="ok",
    )
    mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")

    with (
        patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr,
        patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "sk-from-env",
                "OPENAI_BASE_URL": "https://proxy.example/v1",
                "PATH": "/usr/bin",
            },
            clear=False,
        ),
    ):
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(CodexAdapter(), model=None, max_tokens=256)
        client.invoke("hello")

    merged = mock_run.call_args.kwargs["env"]
    assert merged["OPENAI_API_KEY"] == "sk-from-env"
    assert merged["OPENAI_BASE_URL"] == "https://proxy.example/v1"
    assert "ANTHROPIC_API_KEY" not in merged


@patch("integrations.llm_cli.runner.subprocess.run")
def test_cli_backed_client_caches_probe_between_invokes(mock_run: MagicMock) -> None:
    from integrations.llm_cli.runner import CLIBackedLLMClient

    mock_adapter = MagicMock()
    mock_adapter.name = "codex"
    mock_adapter.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/codex",
        logged_in=True,
        detail="ok",
    )
    mock_adapter.build.return_value = MagicMock(
        argv=["/usr/bin/codex", "exec", "-"],
        stdin="hello",
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.parse.return_value = "answer"
    mock_adapter.explain_failure.return_value = "fail"

    mock_run.return_value = MagicMock(returncode=0, stdout="answer\n", stderr="")

    with patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr:
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter, model="codex", max_tokens=256)
        client.invoke("a")
        client.invoke("b")

    assert mock_adapter.detect.call_count == 1
    assert mock_adapter.build.call_count == 2
    assert mock_run.call_count == 2


@patch("integrations.llm_cli.runner.subprocess.run")
def test_cli_backed_client_failure_mentions_unclear_auth_probe(mock_run: MagicMock) -> None:
    from integrations.llm_cli.runner import CLIBackedLLMClient

    mock_adapter = MagicMock()
    mock_adapter.name = "claude-code"
    mock_adapter.auth_hint = "Run: claude auth login"
    mock_adapter.binary_env_key = "CLAUDE_CODE_BIN"
    mock_adapter.install_hint = "npm i -g @anthropic-ai/claude-code"
    mock_adapter.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/claude",
        logged_in=None,
        detail="claude auth status failed: unknown command",
    )
    mock_adapter.build.return_value = MagicMock(
        argv=["/usr/bin/claude", "-p"],
        stdin="hello",
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.explain_failure.return_value = "claude -p exited with code 1"

    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="unauthorized")

    with patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr:
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter, model="claude-code", max_tokens=256)
        import pytest

        with pytest.raises(RuntimeError, match="Auth status could not be verified"):
            client.invoke("hello")


@patch("integrations.llm_cli.runner.subprocess.run")
def test_cli_backed_client_invoke_raises_cli_authentication_required_when_logged_out(
    mock_run: MagicMock,
) -> None:
    import pytest

    from integrations.llm_cli.errors import CLIAuthenticationRequired
    from integrations.llm_cli.runner import CLIBackedLLMClient

    mock_adapter = MagicMock()
    mock_adapter.name = "cursor"
    mock_adapter.auth_hint = "Run: agent login."
    mock_adapter.binary_env_key = "CURSOR_BIN"
    mock_adapter.install_hint = "install cursor"
    mock_adapter.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/agent",
        logged_in=False,
        detail="Not logged in.",
    )

    with patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr:
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter, model=None, max_tokens=256)
        with pytest.raises(CLIAuthenticationRequired, match="not authenticated"):
            client.invoke("hello")

    mock_run.assert_not_called()


@patch("integrations.llm_cli.runner.subprocess.run")
def test_cli_backed_client_unclear_auth_no_double_period_when_explain_failure_trailing_period(
    mock_run: MagicMock,
) -> None:
    """No double period in the composed error message.

    When stderr contains an auth-related term ('unauthorized'), the failure
    classifier replaces the raw exit-code text with a clean, actionable message
    — so neither the original trailing period nor a '..' can appear.
    """
    from integrations.llm_cli.runner import CLIBackedLLMClient

    mock_adapter = MagicMock()
    mock_adapter.name = "claude-code"
    mock_adapter.auth_hint = "Run: claude auth login"
    mock_adapter.binary_env_key = "CLAUDE_CODE_BIN"
    mock_adapter.install_hint = "npm i -g @anthropic-ai/claude-code"
    mock_adapter.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/claude",
        logged_in=None,
        detail="probe unclear",
    )
    mock_adapter.build.return_value = MagicMock(
        argv=["/usr/bin/claude", "-p"],
        stdin="hello",
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.explain_failure.return_value = "claude -p exited with code 1."

    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")

    with patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr:
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter, model="claude-code", max_tokens=256)
        import pytest

        with pytest.raises(RuntimeError) as exc_info:
            client.invoke("hello")

    msg = str(exc_info.value)
    assert ".." not in msg
    assert "Auth status could not be verified" in msg


def test_detect_uses_codex_bin_env_file(tmp_path) -> None:
    fake_bin = write_fake_runnable_cli_bin(tmp_path, "my-codex")

    with (
        patch.dict(os.environ, {"CODEX_BIN": str(fake_bin)}, clear=False),
        patch("integrations.llm_cli.codex.subprocess.run") as mock_run,
    ):

        def side_effect(args: list[str], **kwargs: object) -> MagicMock:
            assert args[0] == str(fake_bin)
            if args[1] == "--version":
                return _version_proc()
            if args[1] == "login":
                return _login_ok_proc()
            raise AssertionError(args)

        mock_run.side_effect = side_effect
        probe = CodexAdapter().detect()

    assert probe.bin_path == str(fake_bin)
    assert probe.installed is True


@patch("integrations.llm_cli.codex.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/codex")
def test_detect_falls_back_when_codex_bin_invalid(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    with patch.dict(os.environ, {"CODEX_BIN": "/does/not/exist/codex"}, clear=False):

        def side_effect(args: list[str], **kwargs: object) -> MagicMock:
            assert args[0] == "/usr/bin/codex"
            if args[1] == "--version":
                return _version_proc()
            if args[1] == "login":
                return _login_ok_proc()
            raise AssertionError(args)

        mock_run.side_effect = side_effect
        probe = CodexAdapter().detect()

    assert probe.bin_path == "/usr/bin/codex"
    assert probe.installed is True
    mock_which.assert_called()


@patch("integrations.llm_cli.codex.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value=None)
@patch("integrations.llm_cli.codex._fallback_codex_paths", return_value=["/x/codex", "/y/codex"])
@patch("integrations.llm_cli.binary_resolver.is_runnable_binary")
def test_detect_uses_first_runnable_fallback_path(
    mock_is_runnable: MagicMock,
    mock_fallback: MagicMock,
    mock_which: MagicMock,
    mock_run: MagicMock,
) -> None:
    mock_is_runnable.side_effect = lambda p: p == "/y/codex"

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        assert args[0] == "/y/codex"
        if args[1] == "--version":
            return _version_proc()
        if args[1] == "login":
            return _login_ok_proc()
        raise AssertionError(args)

    mock_run.side_effect = side_effect
    probe = CodexAdapter().detect()

    assert probe.bin_path == "/y/codex"
    assert probe.installed is True
    mock_fallback.assert_called_once()
    mock_which.assert_called()


def test_fallback_paths_include_env_and_npm_prefix() -> None:
    npm_prefix_bin_dirs.cache_clear()
    with (
        patch("integrations.llm_cli.binary_resolver.sys.platform", "linux"),
        patch.dict(
            os.environ,
            {
                "PNPM_HOME": "/pnpm/home",
                "XDG_DATA_HOME": "/xdg/data",
                "npm_config_prefix": "/custom/npm",
            },
            clear=False,
        ),
    ):
        paths = _fallback_codex_paths()

    normalized = _posix_path_set(paths)
    assert "/pnpm/home/codex" in normalized
    assert "/xdg/data/pnpm/codex" in normalized
    assert "/custom/npm/bin/codex" in normalized


def test_fallback_paths_include_macos_defaults() -> None:
    npm_prefix_bin_dirs.cache_clear()
    with (
        patch("integrations.llm_cli.binary_resolver.sys.platform", "darwin"),
        patch.dict(os.environ, {}, clear=False),
    ):
        paths = _fallback_codex_paths()

    normalized = _posix_path_set(paths)
    assert "/opt/homebrew/bin/codex" in normalized
    assert "/usr/local/bin/codex" in normalized
    assert (Path.home() / ".local/bin/codex").as_posix() in normalized
    assert (Path.home() / ".npm-global/bin/codex").as_posix() in normalized
    assert (Path.home() / ".volta/bin/codex").as_posix() in normalized


def test_fallback_paths_include_windows_defaults() -> None:
    npm_prefix_bin_dirs.cache_clear()
    with (
        patch("integrations.llm_cli.binary_resolver.sys.platform", "win32"),
        patch.dict(
            os.environ,
            {
                "APPDATA": r"C:\Users\me\AppData\Roaming",
                "LOCALAPPDATA": r"C:\Users\me\AppData\Local",
            },
            clear=False,
        ),
    ):
        paths = _fallback_codex_paths()

    normalized = {p.replace("\\", "/") for p in paths}
    assert "C:/Users/me/AppData/Roaming/npm/codex.cmd" in normalized
    assert "C:/Users/me/AppData/Roaming/npm/codex.exe" in normalized
    assert "C:/Users/me/AppData/Roaming/npm/codex.ps1" in normalized
    assert "C:/Users/me/AppData/Local/Programs/codex/codex.cmd" in normalized
    assert "C:/Users/me/AppData/Local/Programs/codex/codex.exe" in normalized


def test_npm_prefix_bin_dirs_windows_uses_prefix_root() -> None:
    npm_prefix_bin_dirs.cache_clear()
    with (
        patch("integrations.llm_cli.binary_resolver.sys.platform", "win32"),
        patch.dict(os.environ, {"NPM_CONFIG_PREFIX": r"C:\npm\prefix"}, clear=False),
    ):
        dirs = npm_prefix_bin_dirs()
    assert dirs == (r"C:\npm\prefix",)


def test_npm_prefix_bin_dirs_unix_uses_prefix_bin() -> None:
    npm_prefix_bin_dirs.cache_clear()
    with (
        patch("integrations.llm_cli.binary_resolver.sys.platform", "linux"),
        patch.dict(os.environ, {"NPM_CONFIG_PREFIX": "/opt/npm"}, clear=False),
    ):
        dirs = npm_prefix_bin_dirs()
    assert tuple(Path(d).as_posix() for d in dirs) == ("/opt/npm/bin",)


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/codex")
def test_codex_default_exec_timeout_is_shorter(mock_which) -> None:
    """Default timeout is asserted without requiring a real codex binary on CI PATH."""
    inv = CodexAdapter().build(prompt="p", model=None, workspace="")
    assert inv.timeout_sec == 300.0
    mock_which.assert_called()


def test_diagnose_binary_path_missing_file(tmp_path: Path) -> None:
    result = diagnose_binary_path(str(tmp_path / "no-such-binary"))
    assert result is not None
    assert "does not exist" in result


def test_diagnose_binary_path_valid_executable(tmp_path: Path) -> None:
    exe = write_fake_runnable_cli_bin(tmp_path, "my-bin")
    assert diagnose_binary_path(str(exe)) is None


def test_diagnose_binary_path_not_executable(tmp_path: Path) -> None:
    f = tmp_path / "not-executable"
    f.write_bytes(b"")
    os.chmod(f, 0o600)
    result = diagnose_binary_path(str(f))
    if sys.platform != "win32":
        assert result is not None
        assert "not executable" in result


def test_diagnose_binary_path_broken_symlink_windows_skip(tmp_path: Path) -> None:
    """Skip gracefully on Windows hosts where symlink creation requires elevation."""
    link = tmp_path / "broken-link-win"
    try:
        link.symlink_to(tmp_path / "ghost")
    except (OSError, NotImplementedError):
        return  # symlinks not available on this runner; skip
    result = diagnose_binary_path(str(link))
    assert result is not None
    assert "broken symlink" in result


def test_resolve_cli_binary_warns_on_broken_symlink(tmp_path: Path, caplog) -> None:
    from integrations.llm_cli.binary_resolver import resolve_cli_binary

    link = tmp_path / "broken-codex"
    try:
        link.symlink_to(tmp_path / "ghost")
    except (OSError, NotImplementedError):
        return  # symlinks not available on this runner; skip

    with (
        patch.dict(os.environ, {"CODEX_BIN": str(link)}, clear=False),
        patch("integrations.llm_cli.binary_resolver.shutil.which", return_value=None),
        caplog.at_level(logging.WARNING, logger="integrations.llm_cli.binary_resolver"),
    ):
        result = resolve_cli_binary(
            explicit_env_key="CODEX_BIN",
            binary_names=("codex",),
            fallback_paths=[],
        )

    assert result is None
    assert any("broken symlink" in r.message for r in caplog.records)


def test_codex_cli_registry_entry() -> None:
    from integrations.llm_cli.registry import get_cli_provider_registration

    reg = get_cli_provider_registration("codex")
    assert reg is not None
    assert reg.model_env_key == "CODEX_MODEL"
    assert reg.adapter_factory().name == "codex"


def test_parse_returns_stripped_stdout() -> None:
    adapter = CodexAdapter()
    assert adapter.parse(stdout="  hello world  \n", stderr="", returncode=0) == "hello world"


def test_parse_raises_on_empty_stdout() -> None:
    adapter = CodexAdapter()
    with pytest.raises(RuntimeError, match="empty output"):
        adapter.parse(stdout="  ", stderr="", returncode=0)


def test_parse_raises_on_empty_stdout_surfaces_stderr() -> None:
    adapter = CodexAdapter()
    with pytest.raises(RuntimeError, match="some stderr detail"):
        adapter.parse(stdout="", stderr="some stderr detail", returncode=0)


def test_the_probe_and_the_build_agree_on_when_the_hosted_route_applies() -> None:
    """With only OPENAI_BASE_URL set, neither the probe nor the build uses the account token."""
    from config.account import AccountLLMRoute
    from integrations.llm_cli import codex

    route = AccountLLMRoute(base_url="https://app.example/api/llm/v1", model="gpt-5.6-sol")
    base = {k: v for k, v in os.environ.items() if not k.startswith("OPENAI_")}
    with (
        patch.dict(os.environ, {**base, "OPENAI_BASE_URL": "https://proxy.example/v1"}, clear=True),
        patch("config.account.account_llm_route", return_value=route),
        patch("config.account.resolve_account_token", return_value="osre_gw_org.tok"),
    ):
        assert codex._hosted_route_applies() is False
    with (
        patch.dict(os.environ, base, clear=True),
        patch("config.account.account_llm_route", return_value=route),
        patch("config.account.resolve_account_token", return_value="osre_gw_org.tok"),
    ):
        assert codex._hosted_route_applies() is True
