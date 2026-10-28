"""Tests for the Antigravity CLI adapter (detect / build / parse / env forwarding).

Mirrors ``test_gemini_cli_adapter.py`` but covers the breaking differences:
``agy`` returns plain text (no ``--output-format`` JSON envelope), does not
expose ``--model`` in headless mode, and uses ``--print-timeout {N}s``. The
adapter pins ``min_version = "1.0.1"`` (1.0.0 had OAuth hangs).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from integrations.llm_cli.antigravity_cli import (
    _PROBE_TIMEOUT_SEC,
    AntigravityCLIAdapter,
    _fallback_antigravity_cli_paths,
)
from integrations.llm_cli.binary_resolver import npm_prefix_bin_dirs
from integrations.llm_cli.subprocess_env import build_cli_subprocess_env


def _posix_path_set(paths: list[str]) -> set[str]:
    return {Path(p).as_posix() for p in paths}


def _version_proc(version: str = "1.0.1") -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = f"{version}\n"
    m.stderr = ""
    return m


def _auth_ok_proc() -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = "ok\n"
    m.stderr = ""
    return m


@patch("integrations.llm_cli.antigravity_cli.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_logged_in(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/agy"
    mock_run.side_effect = [_version_proc(), _auth_ok_proc()]

    probe = AntigravityCLIAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert probe.bin_path == "/usr/bin/agy"
    assert probe.version == "1.0.1"


@patch("integrations.llm_cli.antigravity_cli.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_not_authenticated(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/agy"
    auth = MagicMock()
    auth.returncode = 1
    auth.stdout = ""
    auth.stderr = "Authentication required"
    mock_run.side_effect = [_version_proc(), auth]

    with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
        probe = AntigravityCLIAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is False


@patch("integrations.llm_cli.antigravity_cli.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_uses_api_key_fallback(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/agy"
    auth = MagicMock()
    auth.returncode = 1
    auth.stdout = ""
    auth.stderr = "Authentication required"
    mock_run.side_effect = [_version_proc(), auth]

    with patch.dict(os.environ, {"GEMINI_API_KEY": "gk-test"}, clear=False):
        probe = AntigravityCLIAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert "GEMINI_API_KEY fallback" in probe.detail


@patch("integrations.llm_cli.antigravity_cli.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_unclear_auth_on_network_error(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/agy"
    auth = MagicMock()
    auth.returncode = 2
    auth.stdout = ""
    auth.stderr = "network unreachable"
    mock_run.side_effect = [_version_proc(), auth]

    with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
        probe = AntigravityCLIAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is None
    assert "Network error" in probe.detail


@patch("integrations.llm_cli.antigravity_cli.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_flags_outdated_version(mock_which: MagicMock, mock_run: MagicMock) -> None:
    mock_which.return_value = "/usr/bin/agy"
    mock_run.side_effect = [_version_proc("1.0.0"), _auth_ok_proc()]

    probe = AntigravityCLIAdapter().detect()

    assert probe.installed is True
    assert probe.version == "1.0.0"
    assert "below tested minimum" in probe.detail
    assert "agy update" in probe.detail


@patch("integrations.llm_cli.antigravity_cli.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_upgrade_note_survives_auth_env_fallback(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    # Regression guard: on agy < 1.0.1 with a failing auth probe AND
    # GEMINI_API_KEY set, the env-fallback path overwrites auth_detail —
    # the upgrade note must still surface so the user knows to run `agy update`.
    mock_which.return_value = "/usr/bin/agy"
    auth = MagicMock()
    auth.returncode = 1
    auth.stdout = ""
    auth.stderr = "Authentication required"
    mock_run.side_effect = [_version_proc("1.0.0"), auth]

    with patch.dict(os.environ, {"GEMINI_API_KEY": "gk-test"}, clear=False):
        probe = AntigravityCLIAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert "GEMINI_API_KEY fallback" in probe.detail
    assert "below tested minimum" in probe.detail


@patch("integrations.llm_cli.antigravity_cli.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_version_command_fails(_mock_which: MagicMock, mock_run: MagicMock) -> None:
    _mock_which.return_value = "/usr/bin/agy"
    m = MagicMock()
    m.returncode = 1
    m.stdout = ""
    m.stderr = "some error\n"
    mock_run.return_value = m

    probe = AntigravityCLIAdapter().detect()

    assert probe.installed is False
    assert probe.logged_in is None


@patch("integrations.llm_cli.antigravity_cli.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_version_timeout_expired(_mock_which: MagicMock, mock_run: MagicMock) -> None:
    _mock_which.return_value = "/usr/bin/agy"
    mock_run.side_effect = subprocess.TimeoutExpired(
        cmd=["/usr/bin/agy", "--version"], timeout=_PROBE_TIMEOUT_SEC
    )

    probe = AntigravityCLIAdapter().detect()

    assert probe.installed is False
    assert probe.logged_in is None
    assert probe.bin_path is None
    assert "could not run" in probe.detail.lower()


@patch(
    "integrations.llm_cli.antigravity_cli._fallback_antigravity_cli_paths",
    return_value=[],
)
@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value=None)
def test_detect_not_installed(_mock_which: MagicMock, _mock_fallback: MagicMock) -> None:
    probe = AntigravityCLIAdapter().detect()
    assert probe.installed is False
    assert probe.logged_in is None
    assert probe.bin_path is None
    assert "not found" in probe.detail.lower()


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/agy")
def test_build_basic_invocation(_mock_which: MagicMock) -> None:
    inv = AntigravityCLIAdapter().build(prompt="explain this alert", model=None, workspace="")
    assert inv.argv[0] == "/usr/bin/agy"
    assert "-p" in inv.argv
    assert "--print-timeout" in inv.argv
    # Default timeout: shared DEFAULT_EXEC_TIMEOUT_SEC (300s)
    idx = inv.argv.index("--print-timeout")
    assert inv.argv[idx + 1] == "300s"
    assert inv.stdin is None
    # subprocess timeout has +10s buffer over agy's own --print-timeout
    assert inv.timeout_sec == 310.0


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/agy")
def test_build_omits_model_and_output_format_and_stateful_flags(
    _mock_which: MagicMock,
) -> None:
    # ``agy`` 1.0.1 does not expose --model or --output-format in headless ``-p``
    # mode; the adapter must never pass them. Stateful flags break opensre's
    # ephemeral invocation contract.
    inv = AntigravityCLIAdapter().build(prompt="p", model="gemini-3.5-flash", workspace="")
    forbidden = {
        "--model",
        "--output-format",
        "--continue",
        "-c",
        "--conversation",
        "--sandbox",
        "--dangerously-skip-permissions",
    }
    assert not (forbidden & set(inv.argv)), f"argv leaked forbidden flag: {inv.argv}"


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/agy")
def test_build_uses_timeout_override(_mock_which: MagicMock) -> None:
    with patch.dict(os.environ, {"ANTIGRAVITY_CLI_TIMEOUT_SECONDS": "300"}, clear=False):
        inv = AntigravityCLIAdapter().build(prompt="p", model=None, workspace="")
    idx = inv.argv.index("--print-timeout")
    assert inv.argv[idx + 1] == "300s"
    assert inv.timeout_sec == 310.0


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/agy")
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
        inv = AntigravityCLIAdapter().build(prompt="p", model=None, workspace="")

    assert inv.env is not None
    assert inv.env["GEMINI_API_KEY"] == "gk-test"
    assert inv.env["GOOGLE_CLOUD_PROJECT"] == "proj-x"


def test_parse_returns_plain_stdout() -> None:
    adapter = AntigravityCLIAdapter()
    # ``agy`` returns plain text; the adapter must not try to JSON-decode it.
    assert adapter.parse(stdout="  hello world  ", stderr="", returncode=0) == "hello world"
    assert (
        adapter.parse(stdout='{"not":"json-envelope"}', stderr="", returncode=0)
        == '{"not":"json-envelope"}'
    )


def test_explain_failure_includes_returncode_and_stderr() -> None:
    adapter = AntigravityCLIAdapter()
    msg = adapter.explain_failure(stdout="", stderr="auth error", returncode=1)
    assert "1" in msg
    assert "auth error" in msg


def test_fallback_paths_macos() -> None:
    npm_prefix_bin_dirs.cache_clear()
    with (
        patch("integrations.llm_cli.binary_resolver.sys.platform", "darwin"),
        patch.dict(os.environ, {}, clear=False),
    ):
        paths = _fallback_antigravity_cli_paths()

    normalized = _posix_path_set(paths)
    assert "/opt/homebrew/bin/agy" in normalized
    assert "/usr/local/bin/agy" in normalized


def test_antigravity_cli_registry_entry() -> None:
    from integrations.llm_cli.registry import get_cli_provider_registration

    reg = get_cli_provider_registration("antigravity-cli")
    assert reg is not None
    assert reg.model_env_key == "ANTIGRAVITY_CLI_MODEL"
    assert reg.adapter_factory().name == "antigravity-cli"


def test_antigravity_prefix_forwarded_to_subprocess() -> None:
    with patch.dict(
        os.environ,
        {
            "ANTIGRAVITY_CLI_BIN": "/usr/bin/agy",
            "ANTIGRAVITY_CLI_TIMEOUT_SECONDS": "240",
            "GOOGLE_CLOUD_PROJECT": "proj-x",
        },
        clear=False,
    ):
        env = build_cli_subprocess_env(None)

    assert env["ANTIGRAVITY_CLI_BIN"] == "/usr/bin/agy"
    assert env["ANTIGRAVITY_CLI_TIMEOUT_SECONDS"] == "240"
    assert env["GOOGLE_CLOUD_PROJECT"] == "proj-x"


@patch("integrations.llm_cli.antigravity_cli.subprocess.run")
@patch("integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_auth_probe_uses_filtered_subprocess_env(
    mock_which: MagicMock, mock_run: MagicMock
) -> None:
    mock_which.return_value = "/usr/bin/agy"
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
        AntigravityCLIAdapter().detect()

    env = mock_run.call_args_list[1].kwargs["env"]
    assert env["PATH"] == "/usr/bin"
    assert env["GOOGLE_CLOUD_PROJECT"] == "proj-x"
    assert env["GEMINI_API_KEY"] == "gk-test"
    assert "RANDOM_SECRET" not in env


def test_antigravity_cli_model_forwarded_to_subprocess() -> None:
    """ANTIGRAVITY_CLI_MODEL must reach CLI subprocesses via the safe-prefix allowlist."""
    with patch.dict(
        os.environ,
        {
            "ANTIGRAVITY_CLI_MODEL": "gemini-3.5-flash",
        },
        clear=False,
    ):
        env = build_cli_subprocess_env(None)

    assert env["ANTIGRAVITY_CLI_MODEL"] == "gemini-3.5-flash"


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/agy")
def test_antigravity_build_absent_env_uses_defaults(_mock_which: MagicMock) -> None:
    """When ANTIGRAVITY_CLI_BIN, _MODEL, and _TIMEOUT_SECONDS are all absent, build()
    resolves the binary via PATH and uses the default 300s timeout."""
    env_strip = {k: v for k, v in os.environ.items() if not k.startswith("ANTIGRAVITY_CLI_")}
    with patch.dict(os.environ, env_strip, clear=True):
        inv = AntigravityCLIAdapter().build(prompt="test prompt", model=None, workspace="")

    assert inv.argv[0] == "/usr/bin/agy"
    idx = inv.argv.index("--print-timeout")
    assert inv.argv[idx + 1] == "300s"
    assert inv.timeout_sec == 310.0


@patch("integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/agy")
def test_antigravity_empty_model_env_treated_as_absent(_mock_which: MagicMock) -> None:
    """An empty ANTIGRAVITY_CLI_MODEL must not produce a --model flag in argv,
    consistent with how other CLI providers treat empty model env vars."""
    with patch.dict(os.environ, {"ANTIGRAVITY_CLI_MODEL": ""}, clear=False):
        inv = AntigravityCLIAdapter().build(prompt="p", model="", workspace="")

    assert "--model" not in inv.argv


def test_parse_returns_stripped_stdout() -> None:
    adapter = AntigravityCLIAdapter()
    assert adapter.parse(stdout="  hello world  \n", stderr="", returncode=0) == "hello world"


def test_parse_raises_on_empty_stdout() -> None:
    import pytest

    adapter = AntigravityCLIAdapter()
    with pytest.raises(RuntimeError, match="empty output"):
        adapter.parse(stdout="  ", stderr="", returncode=0)


def test_parse_raises_on_empty_stdout_surfaces_stderr() -> None:
    import pytest

    adapter = AntigravityCLIAdapter()
    with pytest.raises(RuntimeError, match="some stderr detail"):
        adapter.parse(stdout="", stderr="some stderr detail", returncode=0)
