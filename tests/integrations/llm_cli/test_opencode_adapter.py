"""Tests for the OpenCode CLI adapter (detect / build / failure / auth detection / fallback paths)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from integrations.llm_cli.binary_resolver import npm_prefix_bin_dirs
from integrations.llm_cli.opencode import (
    OpenCodeAdapter,
    _fallback_opencode_paths,
    _parse_opencode_auth_list_output,
    _probe_opencode_auth_via_cli,
)


def _posix_path_set(paths: list[str]) -> set[str]:
    """Normalize paths for cross-platform assertions (Windows backslashes -> forward slashes)."""
    return {Path(p).as_posix() for p in paths}


# ---------------------------------------------------------------------------
# `opencode auth list` output parsing (multi-provider: file + env)
# ---------------------------------------------------------------------------


def test_parse_auth_list_file_credentials_only() -> None:
    raw = """
┌  Credentials ~/.local/share/opencode/auth.json
│
●  OpenCode Go api
│
└  1 credentials
"""
    logged_in, detail = _parse_opencode_auth_list_output(raw, "")
    assert logged_in is True
    assert "1 credential group" in detail


def test_parse_auth_list_env_provider_only() -> None:
    raw = """
┌  Credentials ~/.local/share/opencode/auth.json
│
└  0 credentials

┌  Environment
│
●  Anthropic ANTHROPIC_API_KEY
│
└  1 environment variable
"""
    logged_in, detail = _parse_opencode_auth_list_output(raw, "")
    assert logged_in is True
    assert "1 environment provider" in detail


def test_parse_auth_list_fully_unauthenticated() -> None:
    raw = """
┌  Credentials ~/.local/share/opencode/auth.json
│
└  0 credentials
"""
    logged_in, detail = _parse_opencode_auth_list_output(raw, "")
    assert logged_in is False
    assert "no file credentials" in detail


def test_parse_auth_list_strips_ansi() -> None:
    raw = "\x1b[0m\n└  \x1b[90m0 credentials\x1b[0m\n"
    logged_in, detail = _parse_opencode_auth_list_output(raw, "")
    assert logged_in is False


def test_parse_auth_list_plural_environment_variables() -> None:
    raw = """
└  0 credentials
└  2 environment variables
"""
    logged_in, detail = _parse_opencode_auth_list_output(raw, "")
    assert logged_in is True
    assert "2 environment" in detail


def test_parse_auth_list_missing_credentials_line() -> None:
    logged_in, detail = _parse_opencode_auth_list_output("no summary here", "")
    assert logged_in is None
    assert "missing credentials summary" in detail


@patch("integrations.llm_cli.opencode.subprocess.run")
def test_probe_auth_via_cli_success(mock_run: MagicMock) -> None:
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = "└  1 credentials\n"
    proc.stderr = ""
    mock_run.return_value = proc

    logged_in, detail = _probe_opencode_auth_via_cli("/bin/opencode")
    assert logged_in is True
    mock_run.assert_called_once()
    call_kw = mock_run.call_args.kwargs
    assert call_kw["env"]["NO_COLOR"] == "1"
    argv = mock_run.call_args[0][0]
    assert argv == ["/bin/opencode", "auth", "list"]


@patch("integrations.llm_cli.opencode.subprocess.run")
def test_probe_auth_via_cli_nonzero_exit(mock_run: MagicMock) -> None:
    proc = MagicMock()
    proc.returncode = 2
    proc.stdout = ""
    proc.stderr = "boom"
    mock_run.return_value = proc

    logged_in, detail = _probe_opencode_auth_via_cli("/bin/opencode")
    assert logged_in is None
    assert "failed" in detail.lower()


@patch("integrations.llm_cli.opencode.subprocess.run")
def test_probe_auth_via_cli_timeout(mock_run: MagicMock) -> None:
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["x"], timeout=1.0)

    logged_in, detail = _probe_opencode_auth_via_cli("/bin/opencode")
    assert logged_in is None
    assert "timed out" in detail.lower()


# ---------------------------------------------------------------------------
# detect() tests
# ---------------------------------------------------------------------------


def _version_proc() -> MagicMock:
    """Mock successful version command response."""
    m = MagicMock()
    m.returncode = 0
    m.stdout = "opencode 1.2.3\n"
    m.stderr = ""
    return m


@patch("integrations.llm_cli.opencode.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_installed_and_authenticated(mock_which: MagicMock, mock_run: MagicMock) -> None:
    """Should detect installed binary and authenticated user."""
    mock_which.return_value = "/usr/bin/opencode"
    mock_run.return_value = _version_proc()

    with patch(
        "integrations.llm_cli.opencode._probe_opencode_auth_via_cli",
        return_value=(True, "Authenticated"),
    ):
        probe = OpenCodeAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert probe.bin_path == "/usr/bin/opencode"
    assert probe.version == "1.2.3"


@patch("integrations.llm_cli.opencode.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_installed_not_authenticated(mock_which: MagicMock, mock_run: MagicMock) -> None:
    """Should detect installed binary but user not authenticated."""
    mock_which.return_value = "/usr/bin/opencode"
    mock_run.return_value = _version_proc()

    with patch(
        "integrations.llm_cli.opencode._probe_opencode_auth_via_cli",
        return_value=(False, "Not authenticated"),
    ):
        probe = OpenCodeAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is False


@patch("integrations.llm_cli.opencode._fallback_opencode_paths", return_value=[])
@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value=None)
def test_detect_not_installed(mock_which: MagicMock, mock_fallback: MagicMock) -> None:
    """Should detect that binary is not installed."""
    probe = OpenCodeAdapter().detect()

    assert probe.installed is False
    assert probe.logged_in is None
    assert probe.bin_path is None
    assert "not found" in probe.detail.lower()


@patch("integrations.llm_cli.opencode.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_version_command_fails(mock_which: MagicMock, mock_run: MagicMock) -> None:
    """Should return installed=False when version command fails."""
    mock_which.return_value = "/usr/bin/opencode"
    m = MagicMock()
    m.returncode = 1
    m.stdout = ""
    m.stderr = "some error\n"
    mock_run.return_value = m

    probe = OpenCodeAdapter().detect()

    assert probe.installed is False
    assert probe.logged_in is None


@patch("integrations.llm_cli.opencode.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_version_os_error(mock_which: MagicMock, mock_run: MagicMock) -> None:
    """Should handle OSError when running version command."""
    mock_which.return_value = "/usr/bin/opencode"
    mock_run.side_effect = OSError("not found")

    probe = OpenCodeAdapter().detect()

    assert probe.installed is False
    assert probe.logged_in is None


@patch("integrations.llm_cli.opencode.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_version_timeout_expired(mock_which: MagicMock, mock_run: MagicMock) -> None:
    """Should handle timeout when version command hangs."""
    import subprocess

    mock_which.return_value = "/usr/bin/opencode"
    mock_run.side_effect = subprocess.TimeoutExpired(
        cmd=["/usr/bin/opencode", "--version"], timeout=8.0
    )

    probe = OpenCodeAdapter().detect()

    assert probe.installed is False
    assert probe.logged_in is None
    assert probe.bin_path is None
    assert "could not run" in probe.detail.lower()
    assert "--version" in probe.detail


# ---------------------------------------------------------------------------
# build() tests
# ---------------------------------------------------------------------------


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/opencode")
def test_build_basic_invocation(mock_which: MagicMock) -> None:
    """Should build correct basic command without model flag."""
    inv = OpenCodeAdapter().build(prompt="explain this alert", model=None, workspace="")

    assert inv.argv[0] == "/usr/bin/opencode"
    assert "run" in inv.argv
    assert inv.stdin == "explain this alert"
    assert inv.timeout_sec == 300.0


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/opencode")
def test_build_adds_model_flag(mock_which: MagicMock) -> None:
    """Should add -m flag when model is provided."""
    inv = OpenCodeAdapter().build(prompt="p", model="openai/gpt-5.4-mini", workspace="")

    assert "-m" in inv.argv
    idx = inv.argv.index("-m")
    assert inv.argv[idx + 1] == "openai/gpt-5.4-mini"


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/opencode")
def test_build_omits_model_flag_when_empty_string(mock_which: MagicMock) -> None:
    """Should omit -m flag when model is empty string."""
    inv = OpenCodeAdapter().build(prompt="p", model="", workspace="")
    assert "-m" not in inv.argv


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/opencode")
def test_build_omits_model_flag_when_none(mock_which: MagicMock) -> None:
    """Should omit -m flag when model is None."""
    inv = OpenCodeAdapter().build(prompt="p", model=None, workspace="")
    assert "-m" not in inv.argv


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/opencode")
def test_build_uses_provided_workspace(mock_which: MagicMock) -> None:
    """Should use provided workspace as working directory."""
    inv = OpenCodeAdapter().build(prompt="p", model=None, workspace="/my/project")
    assert inv.cwd == "/my/project"


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/opencode")
def test_build_defaults_to_cwd_when_workspace_empty(mock_which: MagicMock) -> None:
    """Should default to current working directory when workspace not provided."""
    inv = OpenCodeAdapter().build(prompt="p", model=None, workspace="")
    assert inv.cwd == os.getcwd()


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/opencode")
def test_build_sets_no_color_env(mock_which: MagicMock) -> None:
    """Should set NO_COLOR=1 to disable ANSI colors."""
    inv = OpenCodeAdapter().build(prompt="p", model=None, workspace="")
    assert inv.env is not None
    assert inv.env.get("NO_COLOR") == "1"


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/opencode")
def test_build_forwards_http_llm_env_when_set(mock_which: MagicMock) -> None:
    """Should mirror ``opencode auth list`` env-backed credentials into invocation overrides."""
    with patch.dict(
        os.environ,
        {"ANTHROPIC_API_KEY": "sk-ant-test", "OPENAI_PROJECT_ID": "proj-1"},
        clear=False,
    ):
        inv = OpenCodeAdapter().build(prompt="p", model=None, workspace="")

    assert inv.env is not None
    assert inv.env.get("ANTHROPIC_API_KEY") == "sk-ant-test"
    assert inv.env.get("OPENAI_PROJECT_ID") == "proj-1"


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/opencode")
def test_build_forwards_proxy_env_vars(mock_which: MagicMock) -> None:
    """Should forward proxy environment variables to subprocess."""
    with patch.dict(
        os.environ,
        {
            "HTTP_PROXY": "http://proxy:8080",
            "HTTPS_PROXY": "https://proxy:8080",
            "NO_PROXY": "localhost,127.0.0.1",
        },
        clear=False,
    ):
        inv = OpenCodeAdapter().build(prompt="p", model=None, workspace="")

    assert inv.env["HTTP_PROXY"] == "http://proxy:8080"
    assert inv.env["HTTPS_PROXY"] == "https://proxy:8080"
    assert inv.env["NO_PROXY"] == "localhost,127.0.0.1"


@patch("integrations.llm_cli.opencode._fallback_opencode_paths", return_value=[])
@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value=None)
def test_build_raises_when_binary_not_found(
    mock_which: MagicMock, mock_fallback: MagicMock
) -> None:
    """Should raise RuntimeError when binary cannot be found."""
    import pytest

    with pytest.raises(RuntimeError, match="OpenCode CLI not found"):
        OpenCodeAdapter().build(prompt="p", model=None, workspace="")


# ---------------------------------------------------------------------------
# parse() tests
# ---------------------------------------------------------------------------


def test_parse_returns_stripped_stdout() -> None:
    """Should strip whitespace from stdout."""
    adapter = OpenCodeAdapter()
    result = adapter.parse(stdout="  hello world  \n", stderr="", returncode=0)
    assert result == "hello world"


def test_parse_handles_empty_stdout() -> None:
    """Should raise RuntimeError when stdout is empty."""
    import pytest

    adapter = OpenCodeAdapter()
    with pytest.raises(RuntimeError, match="empty output"):
        adapter.parse(stdout="", stderr="", returncode=0)


def test_parse_ignores_stderr() -> None:
    """Should ignore stderr content and only use stdout."""
    adapter = OpenCodeAdapter()
    result = adapter.parse(stdout="response", stderr="some logs", returncode=0)
    assert result == "response"


# ---------------------------------------------------------------------------
# explain_failure() tests
# ---------------------------------------------------------------------------


def test_explain_failure_includes_returncode_and_stderr() -> None:
    """Should include return code and stderr in error message."""
    adapter = OpenCodeAdapter()
    msg = adapter.explain_failure(stdout="", stderr="auth error", returncode=1)
    assert "1" in msg
    assert "auth error" in msg


def test_explain_failure_falls_back_to_stdout() -> None:
    """Should use stdout when stderr is empty."""
    adapter = OpenCodeAdapter()
    msg = adapter.explain_failure(stdout="some output", stderr="", returncode=2)
    assert "some output" in msg


def test_explain_failure_auth_error() -> None:
    """Should provide helpful auth error message."""
    adapter = OpenCodeAdapter()
    msg = adapter.explain_failure(stdout="", stderr="not authenticated", returncode=1)
    assert "Authentication failed" in msg
    assert "opencode auth login" in msg


def test_explain_failure_model_error() -> None:
    """Should provide helpful model format error message."""
    adapter = OpenCodeAdapter()
    msg = adapter.explain_failure(stdout="", stderr="model not found", returncode=1)
    assert "Model not found" in msg
    assert "provider/model" in msg


def test_explain_failure_rate_limit_error() -> None:
    """Should provide helpful rate limit error message."""
    adapter = OpenCodeAdapter()
    msg = adapter.explain_failure(stdout="", stderr="rate limit exceeded", returncode=1)
    assert "Rate limited" in msg


# ---------------------------------------------------------------------------
# OPENCODE_BIN env override tests
# ---------------------------------------------------------------------------


def test_detect_uses_opencode_bin_env(tmp_path: Path) -> None:
    """Should respect OPENCODE_BIN environment variable."""
    fake_bin = tmp_path / "my-opencode"
    fake_bin.write_bytes(b"")
    os.chmod(fake_bin, 0o700)

    with (
        patch.dict(os.environ, {"OPENCODE_BIN": str(fake_bin)}, clear=False),
        patch("integrations.llm_cli.opencode.subprocess.run") as mock_run,
    ):
        mock_run.return_value = _version_proc()
        with patch(
            "integrations.llm_cli.opencode._probe_opencode_auth_via_cli",
            return_value=(True, "ok"),
        ):
            probe = OpenCodeAdapter().detect()

    assert probe.bin_path == str(fake_bin)
    assert probe.installed is True


@patch("integrations.llm_cli.opencode.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_falls_back_when_bin_env_invalid(mock_which: MagicMock, mock_run: MagicMock) -> None:
    """Should fall back to PATH search when OPENCODE_BIN points to invalid binary."""
    mock_which.return_value = "/usr/bin/opencode"
    mock_run.return_value = _version_proc()

    with (
        patch.dict(os.environ, {"OPENCODE_BIN": "/does/not/exist/opencode"}, clear=False),
        patch(
            "integrations.llm_cli.opencode._probe_opencode_auth_via_cli",
            return_value=(True, "ok"),
        ),
    ):
        probe = OpenCodeAdapter().detect()

    assert probe.bin_path == "/usr/bin/opencode"
    assert probe.installed is True


# ---------------------------------------------------------------------------
# Fallback paths tests (binary resolution, NOT auth.json)
# ---------------------------------------------------------------------------


def test_fallback_paths_macos() -> None:
    """Test binary search paths on macOS (Homebrew, local bins, etc.)."""
    npm_prefix_bin_dirs.cache_clear()
    with (
        patch("integrations.llm_cli.binary_resolver.sys.platform", "darwin"),
        patch.dict(os.environ, {}, clear=False),
    ):
        paths = _fallback_opencode_paths()

    normalized = _posix_path_set(paths)
    # Homebrew paths on Apple Silicon and Intel
    assert "/opt/homebrew/bin/opencode" in normalized
    assert "/usr/local/bin/opencode" in normalized
    # User local bin
    assert (Path.home() / ".local/bin/opencode").as_posix() in normalized
    # npm global bins
    assert (Path.home() / ".npm-global/bin/opencode").as_posix() in normalized
    # Volta (Node version manager)
    assert (Path.home() / ".volta/bin/opencode").as_posix() in normalized


def test_fallback_paths_linux() -> None:
    """Test binary search paths on Linux (npm prefixes, local bins)."""
    npm_prefix_bin_dirs.cache_clear()
    with (
        patch("integrations.llm_cli.binary_resolver.sys.platform", "linux"),
        patch.dict(os.environ, {"npm_config_prefix": "/custom/npm"}, clear=False),
    ):
        paths = _fallback_opencode_paths()

    normalized = _posix_path_set(paths)
    assert "/custom/npm/bin/opencode" in normalized
    assert (Path.home() / ".local/bin/opencode").as_posix() in normalized
    assert (Path.home() / ".npm-global/bin/opencode").as_posix() in normalized


def test_fallback_paths_windows() -> None:
    """Test binary search paths on Windows (npm, Scoop, local bins)."""
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
        paths = _fallback_opencode_paths()

    normalized = {p.replace("\\", "/") for p in paths}

    # npm install locations (APPDATA)
    assert "C:/Users/me/AppData/Roaming/npm/opencode.cmd" in normalized
    assert "C:/Users/me/AppData/Roaming/npm/opencode.exe" in normalized
    # Note: .ps1 and .bat are NOT added by default_cli_fallback_paths for npm

    # Scoop install location (LOCALAPPDATA/Programs/opencode)
    assert "C:/Users/me/AppData/Local/Programs/opencode/opencode.cmd" in normalized
    assert "C:/Users/me/AppData/Local/Programs/opencode/opencode.exe" in normalized


# ---------------------------------------------------------------------------
# Registry test
# ---------------------------------------------------------------------------


def test_opencode_registry_entry() -> None:
    """Should be properly registered in CLI provider registry."""
    from integrations.llm_cli.registry import get_cli_provider_registration

    reg = get_cli_provider_registration("opencode")
    assert reg is not None
    assert reg.model_env_key == "OPENCODE_MODEL"
    assert reg.adapter_factory().name == "opencode"


# ---------------------------------------------------------------------------
# Integration: Config model options test
# ---------------------------------------------------------------------------


def test_opencode_model_options_in_wizard() -> None:
    """Verify OpenCode model options are properly defined in wizard config."""
    from surfaces.shared.llm_setup.catalog import OPENCODE_MODELS, SUPPORTED_PROVIDERS

    # Find OpenCode provider
    opencode_provider = None
    for provider in SUPPORTED_PROVIDERS:
        if provider.value == "opencode":
            opencode_provider = provider
            break

    assert opencode_provider is not None
    assert opencode_provider.models == OPENCODE_MODELS
    assert opencode_provider.model_env == "OPENCODE_MODEL"
    assert opencode_provider.credential_kind == "cli"

    # Check that first model option is empty string (CLI default)
    assert OPENCODE_MODELS[0].value == ""
    assert "CLI default" in OPENCODE_MODELS[0].label


# ---------------------------------------------------------------------------
# Subprocess env forwarding — OPENCODE_ prefix must be forwarded
# ---------------------------------------------------------------------------


def test_opencode_prefix_forwarded_to_subprocess() -> None:
    """OPENCODE_* environment variables should be forwarded via blanket prefix allowlist."""
    from integrations.llm_cli.runner import _build_subprocess_env

    with patch.dict(
        os.environ,
        {
            "OPENCODE_MODEL": "openai/gpt-5.4-mini",
            "OPENCODE_BIN": "/custom/bin/opencode",
            "OPENCODE_CONFIG": "/custom/config.json",
        },
        clear=False,
    ):
        env = _build_subprocess_env(None)

    assert env["OPENCODE_MODEL"] == "openai/gpt-5.4-mini"
    assert env["OPENCODE_BIN"] == "/custom/bin/opencode"
    assert env["OPENCODE_CONFIG"] == "/custom/config.json"


def test_non_opencode_vars_not_forwarded() -> None:
    """Only OPENCODE_* vars should be forwarded, not arbitrary vars."""
    from integrations.llm_cli.runner import _build_subprocess_env

    with patch.dict(
        os.environ,
        {
            "OPENCODE_MODEL": "test-model",
            "RANDOM_VAR": "should-not-forward",
            "AWS_SECRET_KEY": "should-not-forward",
            "MY_CONFIG": "should-not-forward",
        },
        clear=False,
    ):
        env = _build_subprocess_env(None)

    assert env["OPENCODE_MODEL"] == "test-model"
    assert "RANDOM_VAR" not in env
    assert "AWS_SECRET_KEY" not in env
    assert "MY_CONFIG" not in env


def test_adapter_build_does_not_need_to_forward_opencode_vars() -> None:
    """OpenCode adapter should NOT manually forward OPENCODE_* vars (runner handles it)."""
    with (
        patch.dict(
            os.environ,
            {
                "OPENCODE_MODEL": "openai/gpt-5.4-mini",
                "OPENCODE_CONFIG": "/custom/config.json",
            },
            clear=False,
        ),
        patch(
            "integrations.llm_cli.binary_resolver.shutil.which",
            return_value="/usr/bin/opencode",
        ),
    ):
        inv = OpenCodeAdapter().build(prompt="test", model=None, workspace=".")

        assert "OPENCODE_MODEL" not in inv.env
        assert "OPENCODE_CONFIG" not in inv.env

        assert inv.env.get("NO_COLOR") == "1"


def test_parse_raises_on_empty_stdout_surfaces_stderr() -> None:
    import pytest

    adapter = OpenCodeAdapter()
    with pytest.raises(RuntimeError, match="some stderr detail"):
        adapter.parse(stdout="", stderr="some stderr detail", returncode=0)
