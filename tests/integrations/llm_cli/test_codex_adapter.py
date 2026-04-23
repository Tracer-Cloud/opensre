"""Tests for Codex CLI adapter detection and prompt helpers."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from app.integrations.llm_cli.codex import CodexAdapter
from app.integrations.llm_cli.text import flatten_messages_to_prompt


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


@patch("app.integrations.llm_cli.codex.subprocess.run")
@patch("app.integrations.llm_cli.codex.shutil.which")
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


@patch("app.integrations.llm_cli.codex.subprocess.run")
@patch("app.integrations.llm_cli.codex.shutil.which")
def test_detect_not_logged_in(mock_which: MagicMock, mock_run: MagicMock) -> None:
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


@patch("app.integrations.llm_cli.codex.shutil.which", return_value="/usr/bin/codex")
def test_build_adds_model_flag_when_not_default(mock_which: MagicMock) -> None:
    inv = CodexAdapter().build(prompt="p", model="o3", workspace="")
    assert inv.stdin == "p"
    assert "-m" in inv.argv
    assert inv.argv[-1] == "-"
    idx = inv.argv.index("-m")
    assert inv.argv[idx + 1] == "o3"
    mock_which.assert_called()


@patch("app.integrations.llm_cli.runner.subprocess.run")
def test_cli_backed_client_invoke(mock_run: MagicMock) -> None:
    from app.integrations.llm_cli.runner import CLIBackedLLMClient

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

    with patch("app.guardrails.engine.get_guardrail_engine") as gr:
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter, model="codex", max_tokens=256)
        resp = client.invoke("hello")

    assert resp.content == "answer"
    mock_adapter.build.assert_called_once()
    mock_run.assert_called_once()


def test_detect_uses_codex_bin_env_file(tmp_path) -> None:
    fake_bin = tmp_path / "my-codex"
    fake_bin.write_bytes(b"")
    os.chmod(fake_bin, 0o755)

    with (
        patch.dict(os.environ, {"CODEX_BIN": str(fake_bin)}, clear=False),
        patch("app.integrations.llm_cli.codex.subprocess.run") as mock_run,
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


@patch("app.integrations.llm_cli.codex.subprocess.run")
@patch("app.integrations.llm_cli.codex.shutil.which", return_value="/usr/bin/codex")
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
