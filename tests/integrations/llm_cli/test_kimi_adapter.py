"""Tests for Kimi Code CLI adapter."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from app.integrations.llm_cli.kimi import KimiAdapter


def _version_proc() -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = "kimi-cli version: 1.40.0\n"
    m.stderr = ""
    return m


@patch("app.integrations.llm_cli.kimi.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_path_binary_logged_in_env(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/kimi"

    def side_effect(args: list[str], **kwargs: object) -> MagicMock:
        if len(args) >= 2 and args[1] == "--version":
            return _version_proc()
        raise AssertionError(args)

    mock_run.side_effect = side_effect
    with patch.dict(os.environ, {"KIMI_API_KEY": "sk-test"}, clear=False):
        probe = KimiAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert probe.bin_path == "/usr/bin/kimi"
    assert probe.version == "1.40.0"


@patch("app.integrations.llm_cli.kimi.pathlib.Path.exists", return_value=False)
@patch("app.integrations.llm_cli.kimi.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_missing_config_not_logged_in(
    mock_which: MagicMock, mock_run: MagicMock, mock_exists: MagicMock
) -> None:
    mock_which.return_value = "/usr/bin/kimi"
    mock_run.return_value = _version_proc()

    with patch.dict(os.environ, {}, clear=True):
        probe = KimiAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is False


@patch(
    "app.integrations.llm_cli.binary_resolver.shutil.which",
    return_value="/usr/bin/kimi",
)
def test_build_adds_model_flag_and_yolo(mock_which: MagicMock) -> None:
    inv = KimiAdapter().build(prompt="p", model="kimi-k2.5", workspace="")
    assert inv.stdin == "p"
    assert "--yolo" in inv.argv
    assert "--print" in inv.argv
    assert "-m" in inv.argv
    idx = inv.argv.index("-m")
    assert inv.argv[idx + 1] == "kimi-k2.5"
    mock_which.assert_called()


def test_kimi_cli_registry_entry() -> None:
    from app.integrations.llm_cli.registry import get_cli_provider_registration

    reg = get_cli_provider_registration("kimi")
    assert reg is not None
    assert reg.model_env_key == "KIMI_MODEL"
    assert reg.adapter_factory().name == "kimi"


@patch("app.integrations.llm_cli.runner.subprocess.run")
def test_cli_backed_client_invoke_forwards_kimi_env(mock_run: MagicMock) -> None:
    from app.integrations.llm_cli.kimi import KimiAdapter
    from app.integrations.llm_cli.runner import CLIBackedLLMClient

    mock_adapter = MagicMock(spec=KimiAdapter)
    mock_adapter.name = "kimi"
    mock_adapter.detect.return_value = MagicMock(
        installed=True,
        bin_path="/usr/bin/kimi",
        logged_in=True,
        detail="ok",
    )
    mock_adapter.build.return_value = MagicMock(
        argv=["/usr/bin/kimi", "--print", "--yolo"],
        stdin="hello",
        cwd="/tmp",
        env=None,
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
                "KIMI_API_KEY": "sk-kimi",
                "KIMI_BASE_URL": "https://custom.kimi.com",
                "OPENAI_API_KEY": "sk-openai",
            },
            clear=False,
        ),
    ):
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(mock_adapter, model="kimi-k2.5", max_tokens=256)
        resp = client.invoke("hello")

    assert resp.content == "answer"
    env = mock_run.call_args.kwargs["env"]
    assert env["KIMI_API_KEY"] == "sk-kimi"
    assert env["KIMI_BASE_URL"] == "https://custom.kimi.com"
    assert "OPENAI_API_KEY" not in env


def test_parse_and_explain_failure() -> None:
    adapter = KimiAdapter()

    # Test parse
    assert adapter.parse(stdout="  hello world  \n", stderr="", returncode=0) == "hello world"

    import pytest

    with pytest.raises(RuntimeError, match="empty output"):
        adapter.parse(stdout="  ", stderr="", returncode=0)

    # Test explain_failure: Auth error
    fail_auth = adapter.explain_failure(stdout="LLM not set", stderr="", returncode=1)
    assert "Not logged in" in fail_auth
    assert "kimi login" in fail_auth

    fail_401 = adapter.explain_failure(stdout="", stderr="Error code: 401", returncode=1)
    assert "API key invalid" in fail_401

    # Test explain_failure: generic error
    fail_generic = adapter.explain_failure(stdout="", stderr="some error", returncode=1)
    assert "kimi exited with code 1" in fail_generic
    assert "some error" in fail_generic
