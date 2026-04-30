"""Tests for Cursor Agent CLI adapter."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from app.integrations.llm_cli.cursor import CursorAdapter


def _version_proc() -> MagicMock:
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = "2026.04.29-c83a488\n"
    proc.stderr = ""
    return proc


def _status_proc(text: str, returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = text
    proc.stderr = ""
    return proc


def _fallback_proc() -> MagicMock:
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = ""
    proc.stderr = ""
    return proc


@patch("app.integrations.llm_cli.cursor.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_logged_in_via_env(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "agent"

    def side_effect(args, **kwargs):
        if "--version" in args:
            return _version_proc()
        return _fallback_proc()

    mock_run.side_effect = side_effect

    with patch.dict(
        os.environ,
        {"CURSOR_API_KEY": "cursor-test", "CURSOR_BIN": "agent"},
        clear=False,
    ):
        probe = CursorAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert probe.version is not None


@patch("app.integrations.llm_cli.cursor.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_logged_in_via_status(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "agent"

    def side_effect(args, **kwargs):
        if "--version" in args:
            return _version_proc()
        if "status" in args:
            return _status_proc("✓ Logged in as user@example.com\n")
        return _fallback_proc()

    mock_run.side_effect = side_effect

    with patch.dict(
        os.environ,
        {"CURSOR_BIN": "agent", "USERPROFILE": r"C:\Users\test"},
        clear=True,
    ):
        probe = CursorAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert "Logged in as" in probe.detail


@patch("app.integrations.llm_cli.cursor.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_not_logged_in(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "agent"

    def side_effect(args, **kwargs):
        if "--version" in args:
            return _version_proc()
        if "status" in args:
            return _status_proc("Not logged in\n", returncode=1)
        return _fallback_proc()

    mock_run.side_effect = side_effect

    with patch.dict(
        os.environ,
        {"CURSOR_BIN": "agent", "USERPROFILE": r"C:\Users\test"},
        clear=True,
    ):
        probe = CursorAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is False


@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value="agent")
def test_build_adds_trust_workspace_and_model(mock_which: MagicMock) -> None:
    inv = CursorAdapter().build(prompt="hello", model="auto", workspace="/tmp/project")

    assert inv.stdin == "hello"
    assert inv.cwd == "/tmp/project"
    assert "--print" in inv.argv
    assert "--trust" in inv.argv
    assert "--output-format" in inv.argv
    assert "text" in inv.argv
    assert "--workspace" in inv.argv
    assert "/tmp/project" in inv.argv
    assert "--model" in inv.argv
    assert "auto" in inv.argv


def test_cursor_cli_registry_entry() -> None:
    from app.integrations.llm_cli.registry import get_cli_provider_registration

    reg = get_cli_provider_registration("cursor")
    assert reg is not None
    assert reg.model_env_key == "CURSOR_MODEL"
    assert reg.adapter_factory().name == "cursor"


@patch("app.integrations.llm_cli.runner.subprocess.run")
def test_cli_backed_client_invoke_forwards_cursor_env(mock_run: MagicMock) -> None:
    from app.integrations.llm_cli.runner import CLIBackedLLMClient

    mock_adapter = MagicMock(spec=CursorAdapter)
    mock_adapter.name = "cursor"
    mock_adapter.detect.return_value = MagicMock(
        installed=True,
        bin_path="agent",
        logged_in=True,
        detail="ok",
    )
    mock_adapter.build.return_value = MagicMock(
        argv=["agent", "--print", "--trust"],
        stdin="hello",
        cwd="/tmp",
        env=None,
        timeout_sec=30.0,
    )
    mock_adapter.parse.return_value = "answer"
    mock_adapter.explain_failure.return_value = "fail"

    mock_run.return_value = MagicMock(returncode=0, stdout="answer\n", stderr="")

    with (
        patch("app.guardrails.engine.get_guardrail_engine") as guardrails,
        patch.dict(
            os.environ,
            {
                "CURSOR_API_KEY": "cursor-key",
                "CURSOR_MODEL": "auto",
                "OPENAI_API_KEY": "openai-key",
            },
            clear=False,
        ),
    ):
        guardrails.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter, model="auto", max_tokens=256)
        resp = client.invoke("hello")

    assert resp.content == "answer"
    env = mock_run.call_args.kwargs["env"]
    assert env["CURSOR_API_KEY"] == "cursor-key"
    assert env["CURSOR_MODEL"] == "auto"
    assert "OPENAI_API_KEY" not in env


def test_parse_and_explain_failure() -> None:
    adapter = CursorAdapter()

    assert adapter.parse(stdout="  Hello!  \n", stderr="", returncode=0) == "Hello!"

    with pytest.raises(RuntimeError, match="empty output"):
        adapter.parse(stdout=" ", stderr="", returncode=0)

    auth_failure = adapter.explain_failure(
        stdout="",
        stderr="Error: Authentication required",
        returncode=1,
    )
    assert "Not logged in" in auth_failure

    trust_failure = adapter.explain_failure(
        stdout="Workspace Trust Required",
        stderr="",
        returncode=1,
    )
    assert "Workspace trust required" in trust_failure

    model_failure = adapter.explain_failure(
        stdout="Named models unavailable",
        stderr="",
        returncode=1,
    )
    assert "CURSOR_MODEL" in model_failure
