"""Tests for the configure_path() function in install.sh."""

from __future__ import annotations

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
_LOCAL_BIN = ".local/bin"


def _run(
    tmp_path: Path, shell: str, platform: str = "linux", install_dir: str | None = None
) -> subprocess.CompletedProcess[str]:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    idir = install_dir if install_dir is not None else str(fake_home / _LOCAL_BIN)

    script = textwrap.dedent(f"""\
        __fn=$(awk 'p&&/^}}$/{{print;exit}} /^configure_path\\(\\)/{{p=1}} p{{print}}' {INSTALL_SH})
        if [ -z "$__fn" ]; then
            echo "configure_path not found in install.sh" >&2
            exit 1
        fi
        log()  {{ printf '%s\\n' "$*"; }}
        warn() {{ printf 'Warning: %s\\n' "$*" >&2; }}
        eval "$__fn"
        INSTALL_DIR="{idir}" platform="{platform}" HOME="{fake_home}" SHELL="{shell}" configure_path
    """)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_zsh_writes_export_to_zshrc(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/zsh")
    assert result.returncode == 0, result.stderr
    zshrc = tmp_path / "home" / ".zshrc"
    assert zshrc.exists()
    assert _LOCAL_BIN in zshrc.read_text()


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
    assert "export PATH" in result.stdout or "export PATH" in result.stderr


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


def test_post_install_message_mentions_source(tmp_path: Path) -> None:
    result = _run(tmp_path, shell="/bin/zsh")
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "source" in combined


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
