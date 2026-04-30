"""Tests for Codex CLI adapter detection and prompt helpers."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.integrations.llm_cli.binary_resolver import diagnose_binary_path, npm_prefix_bin_dirs
from app.integrations.llm_cli.codex import CodexAdapter, _fallback_codex_paths
from app.integrations.llm_cli.text import flatten_messages_to_prompt


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


@patch("app.integrations.llm_cli.codex.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
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
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
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


@patch("app.integrations.llm_cli.codex.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which")
def test_detect_not_logged_in_exit_zero(mock_which: MagicMock, mock_run: MagicMock) -> None:
    """Some Codex versions may exit 0 while printing 'Not logged in' — must not match 'logged in'."""
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


@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/codex")
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
        env={"CODEX_BIN": "/custom/codex"},
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


@patch("app.integrations.llm_cli.runner.subprocess.run")
def test_cli_backed_client_caches_probe_between_invokes(mock_run: MagicMock) -> None:
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
        client.invoke("a")
        client.invoke("b")

    assert mock_adapter.detect.call_count == 1
    assert mock_adapter.build.call_count == 2
    assert mock_run.call_count == 2


def test_detect_uses_codex_bin_env_file(tmp_path) -> None:
    fake_bin = tmp_path / "my-codex"
    fake_bin.write_bytes(b"")
    os.chmod(fake_bin, 0o700)

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
@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/codex")
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


@patch("app.integrations.llm_cli.codex.subprocess.run")
@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value=None)
@patch(
    "app.integrations.llm_cli.codex._fallback_codex_paths", return_value=["/x/codex", "/y/codex"]
)
@patch("app.integrations.llm_cli.binary_resolver.is_runnable_binary")
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
        patch("app.integrations.llm_cli.binary_resolver.sys.platform", "linux"),
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
        patch("app.integrations.llm_cli.binary_resolver.sys.platform", "darwin"),
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
        patch("app.integrations.llm_cli.binary_resolver.sys.platform", "win32"),
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
        patch("app.integrations.llm_cli.binary_resolver.sys.platform", "win32"),
        patch.dict(os.environ, {"NPM_CONFIG_PREFIX": r"C:\npm\prefix"}, clear=False),
    ):
        dirs = npm_prefix_bin_dirs()
    assert dirs == (r"C:\npm\prefix",)


def test_npm_prefix_bin_dirs_unix_uses_prefix_bin() -> None:
    npm_prefix_bin_dirs.cache_clear()
    with (
        patch("app.integrations.llm_cli.binary_resolver.sys.platform", "linux"),
        patch.dict(os.environ, {"NPM_CONFIG_PREFIX": "/opt/npm"}, clear=False),
    ):
        dirs = npm_prefix_bin_dirs()
    assert tuple(Path(d).as_posix() for d in dirs) == ("/opt/npm/bin",)


@patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value="/usr/bin/codex")
def test_codex_default_exec_timeout_is_shorter(mock_which) -> None:
    """Default timeout is asserted without requiring a real codex binary on CI PATH."""
    inv = CodexAdapter().build(prompt="p", model=None, workspace="")
    assert inv.timeout_sec == 120.0
    mock_which.assert_called()


def test_diagnose_binary_path_missing_file(tmp_path: Path) -> None:
    result = diagnose_binary_path(str(tmp_path / "no-such-binary"))
    assert result is not None
    assert "does not exist" in result


def test_diagnose_binary_path_valid_executable(tmp_path: Path) -> None:
    exe = tmp_path / "my-bin"
    exe.write_bytes(b"")
    os.chmod(exe, 0o700)
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
    from app.integrations.llm_cli.binary_resolver import resolve_cli_binary

    link = tmp_path / "broken-codex"
    try:
        link.symlink_to(tmp_path / "ghost")
    except (OSError, NotImplementedError):
        return  # symlinks not available on this runner; skip

    with (
        patch.dict(os.environ, {"CODEX_BIN": str(link)}, clear=False),
        patch("app.integrations.llm_cli.binary_resolver.shutil.which", return_value=None),
        caplog.at_level(logging.WARNING, logger="app.integrations.llm_cli.binary_resolver"),
    ):
        result = resolve_cli_binary(
            explicit_env_key="CODEX_BIN",
            binary_names=("codex",),
            fallback_paths=[],
        )

    assert result is None
    assert any("broken symlink" in r.message for r in caplog.records)


def test_codex_cli_registry_entry() -> None:
    from app.integrations.llm_cli.registry import get_cli_provider_registration

    reg = get_cli_provider_registration("codex")
    assert reg is not None
    assert reg.model_env_key == "CODEX_MODEL"
    assert reg.adapter_factory().name == "codex"
