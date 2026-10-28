"""Tests for the Gemini CLI adapter (detect / build / parse / env forwarding)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from integrations.llm_cli.binary_resolver import npm_prefix_bin_dirs
from integrations.llm_cli.gemini_cli import (
    _PROBE_TIMEOUT_SEC,
    GeminiCLIAdapter,
    _fallback_gemini_cli_paths,
)
from integrations.llm_cli.subprocess_env import build_cli_subprocess_env


def _posix_path_set(paths: list[str]) -> set[str]:
    return {Path(p).as_posix() for p in paths}


def _version_proc() -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = "gemini-cli 0.1.2\n"
    m.stderr = ""
    return m


def _auth_ok_proc() -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = '{"response":"ok"}\n'
    m.stderr = ""
    return m


@patch("integrations.llm_cli.gemini_cli.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_logged_in(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/gemini"
    mock_run.side_effect = [_version_proc(), _auth_ok_proc()]

    probe = GeminiCLIAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert probe.bin_path == "/usr/bin/gemini"
    assert probe.version == "0.1.2"
    # The probe should always carry the 2026-06-18 sunset notice when installed,
    # pointing users to antigravity-cli (additive — paid Code Assist exempt).
    assert "2026-06-18" in probe.detail
    assert "antigravity-cli" in probe.detail


@patch("integrations.llm_cli.gemini_cli.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_not_authenticated(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/gemini"
    auth = MagicMock()
    auth.returncode = 1
    auth.stdout = ""
    auth.stderr = "Authentication required"
    mock_run.side_effect = [_version_proc(), auth]

    with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
        probe = GeminiCLIAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is False
    # The sunset notice is suppressed on confirmed auth failures so users
    # debugging "Not authenticated" don't read it next to the actual error.
    assert "2026-06-18" not in probe.detail
    assert "antigravity-cli" not in probe.detail


@patch("integrations.llm_cli.gemini_cli.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_not_authenticated_when_json_error_requests_auth(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    mock_which.return_value = "/usr/bin/gemini"
    auth = MagicMock()
    auth.returncode = 41
    auth.stdout = json.dumps(
        {
            "error": {
                "type": "Error",
                "message": "Please set an Auth method in your settings.json or set GEMINI_API_KEY",
                "code": 41,
            }
        }
    )
    auth.stderr = ""
    mock_run.side_effect = [_version_proc(), auth]

    with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
        probe = GeminiCLIAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is False


@patch("integrations.llm_cli.gemini_cli.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_not_authenticated_uses_api_key_fallback(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    mock_which.return_value = "/usr/bin/gemini"
    auth = MagicMock()
    auth.returncode = 1
    auth.stdout = ""
    auth.stderr = "Authentication required"
    mock_run.side_effect = [_version_proc(), auth]

    with patch.dict(os.environ, {"GEMINI_API_KEY": "gk-test"}, clear=False):
        probe = GeminiCLIAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert "GEMINI_API_KEY fallback" in probe.detail


@patch("integrations.llm_cli.gemini_cli.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_unclear_auth(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/gemini"
    auth = MagicMock()
    auth.returncode = 2
    auth.stdout = ""
    auth.stderr = "network unreachable"
    mock_run.side_effect = [_version_proc(), auth]

    with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
        probe = GeminiCLIAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is None
    assert "Network error" in probe.detail
    # Ambiguous auth (network blip, timeout) still surfaces the sunset note —
    # the user's CLI is presumed operational; they need the migration warning.
    assert "2026-06-18" in probe.detail


@patch("integrations.llm_cli.gemini_cli.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_version_command_fails(_mock_which: MagicMock, mock_run: MagicMock) -> None:
    _mock_which.return_value = "/usr/bin/gemini"
    m = MagicMock()
    m.returncode = 1
    m.stdout = ""
    m.stderr = "some error\n"
    mock_run.return_value = m

    probe = GeminiCLIAdapter().detect()

    assert probe.installed is False
    assert probe.logged_in is None


@patch("integrations.llm_cli.gemini_cli.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_version_timeout_expired(_mock_which: MagicMock, mock_run: MagicMock) -> None:
    import subprocess

    _mock_which.return_value = "/usr/bin/gemini"
    mock_run.side_effect = subprocess.TimeoutExpired(
        cmd=["/usr/bin/gemini", "--version"], timeout=_PROBE_TIMEOUT_SEC
    )

    probe = GeminiCLIAdapter().detect()

    assert probe.installed is False
    assert probe.logged_in is None
    assert probe.bin_path is None
    assert "could not run" in probe.detail.lower()


@patch("integrations.llm_cli.gemini_cli._fallback_gemini_cli_paths", return_value=[])
@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value=None)
def test_detect_not_installed(_mock_which: MagicMock, _mock_fallback: MagicMock) -> None:
    probe = GeminiCLIAdapter().detect()
    assert probe.installed is False
    assert probe.logged_in is None
    assert probe.bin_path is None
    assert "not found" in probe.detail.lower()


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/gemini")
def test_build_basic_invocation(_mock_which: MagicMock) -> None:
    inv = GeminiCLIAdapter().build(prompt="explain this alert", model=None, workspace="")
    assert inv.argv[0] == "/usr/bin/gemini"
    assert "-p" in inv.argv
    assert "--output-format" in inv.argv
    assert "json" in inv.argv
    assert inv.stdin is None
    assert inv.timeout_sec == 300.0


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/gemini")
def test_build_uses_timeout_override(_mock_which: MagicMock) -> None:
    with patch.dict(os.environ, {"GEMINI_CLI_TIMEOUT_SECONDS": "180"}, clear=False):
        inv = GeminiCLIAdapter().build(prompt="p", model=None, workspace="")
    assert inv.timeout_sec == 180.0


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/gemini")
def test_build_adds_model_flag(_mock_which: MagicMock) -> None:
    inv = GeminiCLIAdapter().build(prompt="p", model="gemini-2.5-pro", workspace="")
    assert "--model" in inv.argv
    idx = inv.argv.index("--model")
    assert inv.argv[idx + 1] == "gemini-2.5-pro"


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/gemini")
def test_build_forwards_gemini_google_env(_mock_which: MagicMock) -> None:
    with patch.dict(
        os.environ,
        {
            "GEMINI_API_KEY": "gk-test",
            "GOOGLE_CLOUD_PROJECT": "proj-x",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
        },
        clear=False,
    ):
        inv = GeminiCLIAdapter().build(prompt="p", model=None, workspace="")

    assert inv.env is not None
    assert inv.env["GEMINI_API_KEY"] == "gk-test"
    assert inv.env["GOOGLE_CLOUD_PROJECT"] == "proj-x"


def test_parse_json_response() -> None:
    adapter = GeminiCLIAdapter()
    result = adapter.parse(
        stdout=json.dumps({"response": "  hello world  "}),
        stderr="",
        returncode=0,
    )
    assert result == "hello world"


def test_parse_non_json_falls_back_to_stdout() -> None:
    adapter = GeminiCLIAdapter()
    result = adapter.parse(stdout="plain text answer", stderr="", returncode=0)
    assert result == "plain text answer"


def test_parse_raises_runtime_error_on_error_payload() -> None:
    adapter = GeminiCLIAdapter()
    with pytest.raises(RuntimeError, match="token expired"):
        adapter.parse(
            stdout=json.dumps({"error": {"message": "token expired"}}),
            stderr="",
            returncode=0,
        )


def test_explain_failure_includes_returncode_and_stderr() -> None:
    adapter = GeminiCLIAdapter()
    msg = adapter.explain_failure(stdout="", stderr="auth error", returncode=1)
    assert "1" in msg
    assert "auth error" in msg


def test_fallback_paths_macos() -> None:
    npm_prefix_bin_dirs.cache_clear()
    with (
        patch("integrations.llm_cli.binary_resolver.sys.platform", "darwin"),
        patch.dict(os.environ, {}, clear=False),
    ):
        paths = _fallback_gemini_cli_paths()

    normalized = _posix_path_set(paths)
    assert "/opt/homebrew/bin/gemini" in normalized
    assert "/usr/local/bin/gemini" in normalized


def test_fallback_paths_windows() -> None:
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
        paths = _fallback_gemini_cli_paths()

    normalized = {p.replace("\\", "/") for p in paths}
    assert "C:/Users/me/AppData/Roaming/npm/gemini.cmd" in normalized


def test_gemini_cli_registry_entry() -> None:
    from integrations.llm_cli.registry import get_cli_provider_registration

    reg = get_cli_provider_registration("gemini-cli")
    assert reg is not None
    assert reg.model_env_key == "GEMINI_CLI_MODEL"
    assert reg.adapter_factory().name == "gemini-cli"


def test_gemini_google_prefix_forwarded_to_subprocess() -> None:
    with patch.dict(
        os.environ,
        {
            "GEMINI_CLI_MODEL": "gemini-2.5-pro",
            "GEMINI_CLI_BIN": "/usr/bin/gemini",
            "GOOGLE_CLOUD_PROJECT": "proj-x",
        },
        clear=False,
    ):
        env = build_cli_subprocess_env(None)

    assert env["GEMINI_CLI_MODEL"] == "gemini-2.5-pro"
    assert env["GEMINI_CLI_BIN"] == "/usr/bin/gemini"
    assert env["GOOGLE_CLOUD_PROJECT"] == "proj-x"


@patch("integrations.llm_cli.gemini_cli.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_auth_probe_uses_filtered_subprocess_env(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    mock_which.return_value = "/usr/bin/gemini"
    mock_run.side_effect = [_version_proc(), _auth_ok_proc()]

    with patch.dict(
        os.environ,
        {
            "PATH": "/usr/bin",
            "RANDOM_SECRET": "must-not-leak",
            "GOOGLE_CLOUD_PROJECT": "proj-x",
            "GEMINI_API_KEY": "gk-test",
        },
        clear=False,
    ):
        GeminiCLIAdapter().detect()

    env = mock_run.call_args_list[1].kwargs["env"]
    assert env["PATH"] == "/usr/bin"
    assert env["GOOGLE_CLOUD_PROJECT"] == "proj-x"
    assert env["GEMINI_API_KEY"] == "gk-test"
    assert "RANDOM_SECRET" not in env


def test_parse_raises_on_empty_stdout() -> None:
    adapter = GeminiCLIAdapter()
    with pytest.raises(RuntimeError, match="empty output"):
        adapter.parse(stdout="  ", stderr="", returncode=0)


def test_parse_raises_on_empty_stdout_surfaces_stderr() -> None:
    adapter = GeminiCLIAdapter()
    with pytest.raises(RuntimeError, match="some stderr detail"):
        adapter.parse(stdout="", stderr="some stderr detail", returncode=0)
