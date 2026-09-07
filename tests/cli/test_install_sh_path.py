"""Tests for the configure_path() function in install.sh."""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# install.sh is a POSIX shell script that exercises zsh/bash/fish rc-file
# behaviour, and these tests drive it via ``subprocess.run(["bash", "-c", ...])``.
# On the GitHub Actions ``windows-latest`` runner, ``bash`` is resolved to
# ``wsl.exe`` and the runner has no installed WSL distribution — every
# ``_run`` call exits 1 with a "Windows Subsystem for Linux has no installed
# distributions" message and none of the asserted rc files get written.
# Skip the whole module rather than chase a Windows analogue for a Unix-only
# installer script.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "install.sh is POSIX-only; the Windows runner has no usable bash "
        "(resolves to unconfigured WSL), so this module's subprocess-driven "
        "tests cannot run there. See issue #1099."
    ),
)

INSTALL_SH = Path(__file__).parents[2] / "install.sh"
_INSTALL_SH_SHELL = shlex.quote(str(INSTALL_SH))
_LOCAL_BIN = ".local/bin"
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(value: str) -> str:
    return _ANSI_RE.sub("", value)


def _visible_terminal_text(value: str) -> str:
    return _strip_ansi(value).replace("\r", "").replace("\n", "")


def _run(
    tmp_path: Path, shell: str, platform: str = "linux", install_dir: str | None = None
) -> subprocess.CompletedProcess[str]:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    idir = install_dir if install_dir is not None else str(fake_home / _LOCAL_BIN)
    install_sh = _INSTALL_SH_SHELL
    idir_shell = shlex.quote(idir)
    home_shell = shlex.quote(str(fake_home))

    script = textwrap.dedent(f"""\
        __fn=$(awk 'p&&/^}}$/{{print;exit}} /^configure_path\\(\\)/{{p=1}} p{{print}}' {install_sh})
        if [ -z "$__fn" ]; then
            echo "configure_path not found in install.sh" >&2
            exit 1
        fi
        log()  {{ printf '%s\\n' "$*"; }}
        muted() {{ printf '%s\\n' "$*"; }}
        warn() {{ printf 'Warning: %s\\n' "$*" >&2; }}
        eval "$__fn"
        INSTALL_DIR={idir_shell} platform="{platform}" HOME={home_shell} SHELL="{shell}" configure_path
    """)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def _run_logging_snippet(body: str) -> subprocess.CompletedProcess[str]:
    install_sh = _INSTALL_SH_SHELL
    script = textwrap.dedent(f"""\
        eval "$(awk '/^REPO=/{{exit}} {{print}}' {install_sh})"
        eval "$(awk '
            /^[a-z_][a-z_]*\\(\\)/ {{ in_fn=1 }}
            in_fn {{ print }}
            in_fn && /^\\}}$/ {{ in_fn=0 }}
        ' {install_sh})"
        {body}
    """)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_install_sh_logging_falls_back_to_plain_text_when_not_tty() -> None:
    result = _run_logging_snippet(
        """
        warn "check config"
        success "installed"
        muted "[1/4] Fetching metadata"
        """
    )

    assert result.returncode == 0, result.stderr
    assert "\x1b[" not in result.stdout + result.stderr
    assert "Warning: check config" in result.stderr
    assert "Success: installed" in result.stdout
    assert "[1/4] Fetching metadata" in result.stdout


def test_install_sh_die_falls_back_to_plain_text_when_not_tty() -> None:
    result = _run_logging_snippet('die "missing curl"')

    assert result.returncode == 1
    assert "\x1b[" not in result.stderr
    assert "Error: missing curl" in result.stderr


def test_install_sh_defines_tty_aware_ansi_formatting() -> None:
    source = INSTALL_SH.read_text()

    assert "if [ -t 1 ]; then" in source
    assert "COLOR_GREEN=$'\\033[32m'" in source
    assert "COLOR_YELLOW=$'\\033[33m'" in source
    assert "COLOR_RED=$'\\033[31m'" in source
    assert "COLOR_GRAY=$'\\033[90m'" in source
    assert "success()" in source


def test_install_sh_styles_details_gray_and_get_started_yellow() -> None:
    result = _run_logging_snippet(
        """
        COLOR_RESET=$'\\033[0m'
        COLOR_GRAY=$'\\033[90m'
        COLOR_YELLOW=$'\\033[33m'
        BIN_NAME="opensre"
        muted "Checksum verification passed"
        log "${COLOR_YELLOW}Run '${BIN_NAME}' to sign in and get started.${COLOR_RESET}"
        """
    )

    assert result.returncode == 0, result.stderr
    assert "\x1b[90mChecksum verification passed\x1b[0m" in result.stdout
    assert "\x1b[33mRun 'opensre' to sign in and get started.\x1b[0m" in result.stdout


def test_install_sh_prints_concise_install_confirmation() -> None:
    result = _run_logging_snippet(
        """
        BIN_NAME="opensre"
        INSTALL_DIR="/tmp/bin"
        installed_version="2026.4.1"
        print_install_confirmation
        """
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, result.stderr
    assert output == "OpenSRE v2026.4.1 installed successfully to /tmp/bin/opensre\n"


def test_install_sh_auto_launches_account_setup_only_on_a_tty() -> None:
    source = INSTALL_SH.read_text()

    assert "auto_setup_enabled()" in source
    assert "launch_setup_after_install()" in source
    assert "OPENSRE_AUTO_LAUNCH" in source
    assert "[ ! -t 0 ] || [ ! -t 1 ]" in source
    assert '"$binary_path" setup' in source


def test_install_sh_defaults_to_main_build_channel() -> None:
    source = INSTALL_SH.read_text()

    assert 'INSTALL_CHANNEL="${OPENSRE_INSTALL_CHANNEL:-main}"' in source
    assert 'MAIN_RELEASE_TAG="${OPENSRE_MAIN_RELEASE_TAG:-main-build}"' in source
    assert "releases/tags/${MAIN_RELEASE_TAG}" in source
    assert "releases/tags/nightly" not in source


def test_install_sh_defines_progress_helpers() -> None:
    source = INSTALL_SH.read_text()

    for helper in (
        "is_interactive_status_terminal()",
        "animate_dots()",
        "finish_dots()",
        "run_with_dots()",
        "binary_app_root()",
        "stage_binary_app()",
        "activate_staged_binary()",
        "print_binary_diagnostics()",
    ):
        assert helper in source

    assert "OPENSRE_INSTALL_VERBOSE" in source
    assert "\\033[?25h" in source
    assert "\\033[2J" not in source
    assert "preparing installer" not in source
    assert "run_with_progress" not in source
    assert "draw_progress" not in source
    assert "█" not in source
    assert "░" not in source


def test_install_sh_animates_binary_verification_dots() -> None:
    label = "Found opensre binary, verifying it runs"
    result = _run_logging_snippet(
        f"""
        is_interactive_status_terminal() {{ return 0; }}
        run_with_dots {shlex.quote(label)} bash -c 'sleep 0.9'
        """
    )
    frames = {
        _visible_terminal_text(segment)
        for segment in re.split(r"[\r\n]", result.stdout + result.stderr)
        if label in _visible_terminal_text(segment)
    }

    assert result.returncode == 0, result.stderr
    assert {f"{label}.", f"{label}..", f"{label}..."} <= frames


def test_install_sh_verification_dots_are_plain_when_not_tty() -> None:
    result = _run_logging_snippet(
        """
        run_with_dots "Found opensre binary, verifying it runs" true
        """
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, result.stderr
    assert output == "Found opensre binary, verifying it runs...\n"
    assert "\x1b[" not in output
    assert "\r" not in output


def test_install_sh_verify_binary_failure_includes_diagnostics(tmp_path: Path) -> None:
    fake_binary = tmp_path / "opensre"
    fake_binary.write_text("#!/usr/bin/env sh\nexit 42\n", encoding="utf-8")
    fake_binary.chmod(0o755)

    result = _run_logging_snippet(
        f"""
        platform="linux"
        verify_binary_version {shlex.quote(str(fake_binary))}
        """
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1
    assert "Failed to execute opensre --version (exit 42)." in output
    assert "Command output: <empty>" in output
    assert "Binary diagnostics:" in output
    assert str(fake_binary) in output


def test_install_sh_installs_pyinstaller_onedir_app(tmp_path: Path) -> None:
    app_root = tmp_path / "opensre-app"
    app_root.mkdir()
    app_binary = app_root / "opensre"
    app_binary.write_text("#!/usr/bin/env sh\nprintf 'opensre test\\n'\n", encoding="utf-8")
    app_binary.chmod(0o755)
    internal = app_root / "_internal"
    internal.mkdir()
    (internal / "payload.txt").write_text("bundled", encoding="utf-8")
    install_dir = tmp_path / "bin"
    destination = install_dir / "opensre"

    result = _run_logging_snippet(
        f"""
        platform="linux"
        BIN_NAME="opensre"
        INSTALL_DIR={shlex.quote(str(install_dir))}
        staged="$(stage_binary {shlex.quote(str(app_binary))})"
        test -f "$staged"
        test ! -e {shlex.quote(str(app_root))}
        test ! -e {shlex.quote(str(destination))}
        activate_staged_binary "$staged" {shlex.quote(str(destination))}
        test ! -e "$staged"
        test -L {shlex.quote(str(destination))}
        test -x {shlex.quote(str(destination))}
        test -f {shlex.quote(str(install_dir / ".opensre-app" / "_internal" / "payload.txt"))}
        {shlex.quote(str(destination))}
        """
    )

    assert result.returncode == 0, result.stderr
    assert "opensre test" in result.stdout


def test_install_sh_uses_concise_unnumbered_install_messages() -> None:
    source = INSTALL_SH.read_text()

    assert "Downloading OpenSRE main build for ${platform}-${asset_arch}" in source
    assert 'run_with_dots "Fetching and verifying checksum"' in source
    assert 'run_with_dots "Extracting OpenSRE"' in source
    assert 'run_with_dots "Installing OpenSRE"' in source
    assert 'run_with_dots "Checking PATH configuration"' in source
    assert 'muted "Checksum verification passed"' in source
    assert "Run '${BIN_NAME}' to get started!" in source
    assert re.search(r"\[[0-9]+/[0-9]+\]", source) is None
    assert "Extracting %s into %s" not in source
    assert "Found binary at %s" not in source


def test_install_sh_concise_output_sequence() -> None:
    result = _run_logging_snippet(
        """
        INSTALL_CHANNEL="main"
        platform="darwin"
        asset_arch="arm64"
        archive="opensre_main_darwin-arm64.tar.gz"
        tmp_dir="/tmp"
        download_url="https://example.invalid/archive"
        checksum_asset="${archive}.sha256"
        checksum_url="${download_url}.sha256"
        release_json="{}"
        INSTALL_DIR="/tmp/bin"
        BIN_NAME="opensre"
        installed_version="2026.8.26"
        PATH="${INSTALL_DIR}:${PATH}"
        download_to() { return 0; }
        release_has_asset() { return 0; }
        download_and_verify_checksum() { return 0; }

        download_release_archive
        verify_release_checksum
        print_install_confirmation
        ensure_on_path
        log "Run '${BIN_NAME}' to get started!"
        """
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Checksum verification passed",
        "OpenSRE v2026.8.26 installed successfully to /tmp/bin/opensre",
        "PATH already configured",
        "Run 'opensre' to get started!",
    ]
    assert result.stderr.splitlines() == [
        "Downloading OpenSRE main build for darwin-arm64...",
        "Fetching and verifying checksum...",
        "Checking PATH configuration...",
    ]


def test_zsh_writes_export_to_zshrc(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/zsh")
    assert result.returncode == 0, result.stderr
    zshrc = tmp_path / "home" / ".zshrc"
    assert zshrc.exists()
    assert f'export PATH="{tmp_path / "home" / _LOCAL_BIN}:$PATH"' in zshrc.read_text()


def test_bash_linux_writes_to_bashrc(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/bash", platform="linux")
    assert result.returncode == 0, result.stderr
    bashrc = tmp_path / "home" / ".bashrc"
    assert bashrc.exists()
    assert _LOCAL_BIN in bashrc.read_text()


def test_bash_macos_writes_to_bash_profile(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/bash", platform="darwin")
    assert result.returncode == 0, result.stderr
    bash_profile = tmp_path / "home" / ".bash_profile"
    assert bash_profile.exists()
    assert _LOCAL_BIN in bash_profile.read_text()


def test_fish_uses_fish_add_path(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/usr/bin/fish")
    assert result.returncode == 0, result.stderr
    fish_config = tmp_path / "home" / ".config" / "fish" / "config.fish"
    assert fish_config.exists()
    assert "fish_add_path" in fish_config.read_text()


def test_unknown_shell_prints_manual_instructions(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/dash")
    assert result.returncode == 0, result.stderr
    home = tmp_path / "home"
    assert not (home / ".zshrc").exists()
    assert not (home / ".bashrc").exists()
    assert not (home / ".bash_profile").exists()
    assert "Add " in result.stdout
    assert " to PATH to run opensre" in result.stdout


def test_idempotent_no_duplicate_on_rerun(tmp_path: Path) -> None:
    _run(tmp_path, shell="/bin/zsh")
    _run(tmp_path, shell="/bin/zsh")
    content = (tmp_path / "home" / ".zshrc").read_text()
    export_lines = [ln for ln in content.splitlines() if _LOCAL_BIN in ln and "export PATH" in ln]
    assert len(export_lines) == 1


def test_skips_when_install_dir_already_in_rc(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    idir = str(home / _LOCAL_BIN)
    zshrc = home / ".zshrc"
    zshrc.write_text(f'export PATH="$PATH:{idir}"\n')
    original = zshrc.read_text()

    result = _run(tmp_path, shell="/bin/zsh", install_dir=idir)
    assert result.returncode == 0, result.stderr
    assert zshrc.read_text() == original


def test_creates_rc_file_when_missing(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/zsh")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "home" / ".zshrc").exists()


def test_marker_comment_present(tmp_path: Path) -> None:
    _run(tmp_path, shell="/bin/zsh")
    content = (tmp_path / "home" / ".zshrc").read_text()
    assert "# Added by opensre installer" in content


def test_post_install_message_is_concise(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/zsh")
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "PATH configured in " in combined
    assert "source" not in combined


def test_fish_creates_parent_dirs(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/usr/bin/fish")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "home" / ".config" / "fish" / "config.fish").exists()


def test_readds_export_when_marker_present_but_line_removed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    zshrc = home / ".zshrc"
    zshrc.write_text("# Added by opensre installer\n")

    result = _run(tmp_path, shell="/bin/zsh")
    assert result.returncode == 0, result.stderr
    content = zshrc.read_text()
    assert _LOCAL_BIN in content


# ---------------------------------------------------------------------------
# PowerShell post-install output
# ---------------------------------------------------------------------------


def test_install_ps1_contains_setup_hint() -> None:
    """Contract test: the setup hint must be present in install.ps1 source."""
    install_ps1 = Path(__file__).parents[2] / "install.ps1"
    source = install_ps1.read_text()
    assert "$exe setup" in source, (
        "install.ps1 does not contain the setup step "
        '(expected a line with ``$exe setup``, e.g. ``Write-Host "  1. Run  $exe setup"``).'
    )


def _run_ensure_on_path(
    tmp_path: Path,
    path_dirs: list[str],
    platform: str = "linux",
    include_install_dir: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Call the real ensure_on_path() with INSTALL_DIR off PATH.

    ``path_dirs`` becomes the PATH visible to the function; the returned Path
    is the populated install dir (never on that PATH).
    """
    install_dir = tmp_path / "install-dir"
    install_dir.mkdir()
    binary = install_dir / "opensre"
    binary.write_text("#!/usr/bin/env bash\necho opensre-ok\n")
    binary.chmod(0o755)

    effective_path_dirs = [str(install_dir), *path_dirs] if include_install_dir else path_dirs
    path_value = ":".join([*effective_path_dirs, "/usr/bin", "/bin"])
    script = textwrap.dedent(f"""\
        eval "$(awk '
            /^[a-z_][a-z_]*\\(\\)/ {{ in_fn=1 }}
            in_fn {{ print }}
            in_fn && /^\\}}$/ {{ in_fn=0 }}
        ' {_INSTALL_SH_SHELL})"
        log()  {{ printf '%s\\n' "$*"; }}
        warn() {{ printf 'Warning: %s\\n' "$*" >&2; }}
        success() {{ printf 'Success: %s\\n' "$*"; }}
        INSTALL_DIR={shlex.quote(str(install_dir))}
        BIN_NAME="opensre"
        platform="{platform}"
        HOME={shlex.quote(str(tmp_path / "home"))}
        SHELL="/bin/zsh"
        PATH={shlex.quote(path_value)}
        export HOME SHELL PATH
        mkdir -p "$HOME"
        ensure_on_path
    """)
    completed = subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=tmp_path)
    return completed, install_dir


def test_ensure_on_path_reports_existing_configuration(tmp_path: Path) -> None:
    result, _ = _run_ensure_on_path(tmp_path, [], include_install_dir=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["PATH already configured"]
    assert result.stderr.splitlines() == ["Checking PATH configuration..."]


def test_ensure_on_path_does_not_link_into_arbitrary_dependency_bin(tmp_path: Path) -> None:
    dependency_bin = tmp_path / "apache-spark" / "bin"
    dependency_bin.mkdir(parents=True)

    result, _ = _run_ensure_on_path(tmp_path, [str(dependency_bin)])

    assert result.returncode == 0, result.stderr
    assert not (dependency_bin / "opensre").exists()
    assert (tmp_path / "home" / ".zshrc").exists()


def test_ensure_on_path_falls_back_to_rc_update_without_writable_dir(tmp_path: Path) -> None:
    """With no writable dir on PATH, the shell rc fallback still runs."""
    result, _ = _run_ensure_on_path(tmp_path, [])

    assert result.returncode == 0, result.stderr
    zshrc = tmp_path / "home" / ".zshrc"
    assert zshrc.exists()
    assert "install-dir" in zshrc.read_text()


def test_ensure_on_path_ignores_relative_path_entries(tmp_path: Path) -> None:
    """Relative PATH entries (e.g. ``.``) must never receive the symlink."""
    result, _ = _run_ensure_on_path(tmp_path, ["."])

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "opensre").exists()
    assert (tmp_path / "home" / ".zshrc").exists()


def _run_ensure_github_cli(
    *,
    path_dirs: list[Path],
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Drive ``ensure_github_cli`` with a controlled PATH (no real brew/apt)."""
    install_sh = _INSTALL_SH_SHELL
    path_value = ":".join(str(p) for p in path_dirs)
    env_exports = " ".join(
        f"{key}={shlex.quote(value)}" for key, value in (env_extra or {}).items()
    )
    script = textwrap.dedent(f"""\
        eval "$(awk '
            /^[a-z_][a-z_]*\\(\\)/ {{ in_fn=1 }}
            in_fn {{ print }}
            in_fn && /^\\}}$/ {{ in_fn=0 }}
        ' {install_sh})"
        export PATH={shlex.quote(path_value)}
        {env_exports} ensure_github_cli
    """)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_ensure_github_cli_skips_when_gh_present(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\necho gh\n")
    gh.chmod(0o755)

    result = _run_ensure_github_cli(path_dirs=[bin_dir])
    assert result.returncode == 0, result.stderr
    assert "OpenSRE GitHub chat tools" not in result.stderr
    assert "Installing GitHub CLI" not in result.stdout


def test_ensure_github_cli_respects_skip_env(tmp_path: Path) -> None:
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()

    result = _run_ensure_github_cli(
        path_dirs=[empty_bin],
        env_extra={"OPENSRE_SKIP_GH_INSTALL": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert "OPENSRE_SKIP_GH_INSTALL" in result.stderr
    assert "OpenSRE GitHub chat tools" in result.stderr


def test_warm_first_launch_skips_package_smoke_off_darwin() -> None:
    """Linux/Windows have no codesign cache; the installer must not pay for smoke."""
    result = _run_logging_snippet(
        """
        uname() { printf 'Linux\\n'; }
        package_smoke_quiet() { printf 'SMOKE_RAN\\n'; return 0; }
        BIN_NAME=opensre
        warm_first_launch /tmp/opensre
        printf 'done\\n'
        """
    )

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "SMOKE_RAN" not in combined
    assert "Preparing OpenSRE for first launch" not in combined
    assert "done" in result.stdout


def test_warm_first_launch_runs_package_smoke_on_darwin() -> None:
    result = _run_logging_snippet(
        """
        uname() { printf 'Darwin\\n'; }
        package_smoke_quiet() { printf 'SMOKE_RAN\\n'; return 0; }
        BIN_NAME=opensre
        warm_first_launch /tmp/opensre
        """
    )

    assert result.returncode == 0, result.stderr
    assert "SMOKE_RAN" in result.stdout
    assert "Preparing OpenSRE for first launch" in result.stderr


def test_install_release_binary_verifies_and_warms_the_staged_tree_before_activation() -> None:
    """Signature validation is cached per file: check and warm the tree that gets renamed
    into place, and never copy it afterwards (a copy is validated all over again)."""
    source = INSTALL_SH.read_text(encoding="utf-8")
    pipeline = source.split("install_release_binary()")[1].split("\nprint_install_confirmation()")[
        0
    ]
    assert pipeline.index('verify_staged_binary "$staged_path"') < pipeline.index(
        'warm_first_launch "$staged_path"'
    )
    assert pipeline.index('warm_first_launch "$staged_path"') < pipeline.index(
        'activate_staged_binary "$staged_path"'
    )
    assert "cp -R" not in source
    # prepare_and_verify must not invoke warm (comment may still name it).
    prepare = source.split("prepare_and_verify_binary()")[1].split("\nwarm_first_launch()")[0]
    assert "warm_first_launch " not in prepare
    assert "warm_first_launch\n" not in prepare


def test_discarding_a_staged_app_leaves_the_existing_install_alone(tmp_path: Path) -> None:
    app_root = tmp_path / "opensre-app"
    (app_root / "_internal").mkdir(parents=True)
    app_binary = app_root / "opensre"
    app_binary.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    app_binary.chmod(0o755)
    install_dir = tmp_path / "bin"
    existing_app = install_dir / ".opensre-app"
    (existing_app / "_internal").mkdir(parents=True)
    (existing_app / "opensre").write_text("#!/usr/bin/env sh\nprintf 'old\\n'\n", encoding="utf-8")
    (existing_app / "opensre").chmod(0o755)
    destination = install_dir / "opensre"
    destination.symlink_to(existing_app / "opensre")

    result = _run_logging_snippet(
        f"""
        platform="linux"
        BIN_NAME="opensre"
        INSTALL_DIR={shlex.quote(str(install_dir))}
        staged="$(stage_binary {shlex.quote(str(app_binary))})"
        discard_staged_binary "$staged"
        test ! -e "$staged"
        {shlex.quote(str(destination))}
        """
    )

    assert result.returncode == 0, result.stderr
    assert "old" in result.stdout


def test_resign_macos_onedir_parallelizes_nested_libs() -> None:
    """Nested dylib/so signs are independent; main binary stays serial and last."""
    source = INSTALL_SH.read_text(encoding="utf-8")
    assert 'xargs -0 -P "$jobs" -n 1 codesign --force --sign -' in source
    assert 'codesign --force --sign - "$binary_path"' in source
    # Cap avoids disk stampede on large hosts.
    assert 'if [ "$jobs" -gt 4 ]; then' in source
