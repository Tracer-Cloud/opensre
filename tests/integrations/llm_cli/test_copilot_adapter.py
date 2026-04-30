"""Tests for GitHub Copilot CLI adapter detection and prompt helpers."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from app.integrations.llm_cli.copilot import CopilotAdapter, _fallback_copilot_paths
from app.integrations.llm_cli.runner import CLIBackedLLMClient


def _version_proc() -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = "copilot 1.2.3\n"
    m.stderr = ""
    return m


@patch("app.integrations.llm_cli.copilot.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_path_binary_with_headless_token(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/copilot"

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        assert args[0] == "/usr/bin/copilot"
        assert args[1] == "--version"
        return _version_proc()

    mock_run.side_effect = side_effect
    with patch.dict(os.environ, {"COPILOT_GITHUB_TOKEN": "token-value"}, clear=False):
        probe = CopilotAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert probe.bin_path == "/usr/bin/copilot"
    assert probe.version == "1.2.3"


@patch("app.integrations.llm_cli.copilot.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_ambiguous_without_token(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/copilot"
    mock_run.return_value = _version_proc()

    with patch.dict(
        os.environ,
        {
            "COPILOT_GITHUB_TOKEN": "",
            "GH_TOKEN": "",
            "GITHUB_TOKEN": "",
        },
        clear=False,
    ):
        probe = CopilotAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is None


@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/copilot")
def test_build_adds_prompt_and_allow_all(mock_which: MagicMock) -> None:
    inv = CopilotAdapter().build(prompt="p", model="gpt-5.4", workspace="/work")
    assert inv.stdin is None
    assert inv.cwd == "/work"
    assert "-p" in inv.argv
    assert "--allow-all" in inv.argv
    assert "--no-ask-user" in inv.argv
    assert "--model" in inv.argv
    assert inv.env == {"COPILOT_ALLOW_ALL": "true"}
    mock_which.assert_called()


@patch("app.integrations.llm_cli.runner.subprocess.run")
def test_cli_backed_client_forwards_copilot_env(mock_run: MagicMock) -> None:
    mock_adapter = MagicMock()
    mock_adapter.name = "copilot"
    mock_adapter.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/copilot",
        logged_in=True,
        detail="ok",
    )
    mock_adapter.build.return_value = MagicMock(
        argv=["/usr/bin/copilot", "-p", "hello"],
        stdin=None,
        cwd="/tmp",
        env={"COPILOT_ALLOW_ALL": "true"},
        timeout_sec=30.0,
    )
    mock_adapter.parse.return_value = "answer"
    mock_adapter.explain_failure.return_value = "fail"
    mock_run.return_value = MagicMock(returncode=0, stdout="answer\n", stderr="")

    with (
        patch("app.guardrails.engine.get_guardrail_engine") as gr,
        patch.dict(
            os.environ,
            {
                "COPILOT_GITHUB_TOKEN": "copilot-secret",
                "GH_TOKEN": "gh-secret",
                "GITHUB_TOKEN": "github-secret",
                "COPILOT_MODEL": "gpt-5.4",
                "PATH": "/usr/bin",
            },
            clear=False,
        ),
    ):
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter, model="gpt-5.4", max_tokens=256)
        resp = client.invoke("hello")

    assert resp.content == "answer"
    env = mock_run.call_args.kwargs["env"]
    assert env["COPILOT_GITHUB_TOKEN"] == "copilot-secret"
    assert env["GH_TOKEN"] == "gh-secret"
    assert env["GITHUB_TOKEN"] == "github-secret"
    assert env["COPILOT_MODEL"] == "gpt-5.4"


def test_fallback_paths_include_copilot_binary_name() -> None:
    paths = _fallback_copilot_paths()
    assert any(path.endswith("copilot") or path.endswith("copilot.cmd") for path in paths)
