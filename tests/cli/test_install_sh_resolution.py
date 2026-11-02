"""Tests for install directory resolution in install.sh."""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# Same Windows-skip rationale as ``test_install_sh_path.py`` — install.sh is
# POSIX-only and the GitHub Actions ``windows-latest`` runner has no usable
# bash. See issue #1099.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "install.sh is POSIX-only; the Windows runner has no usable bash "
        "(resolves to unconfigured WSL), so this module's subprocess-driven "
        "tests cannot run there. See issue #1099."
    ),
)

INSTALL_SH = Path(__file__).parents[2] / "install.sh"


def _run_resolution(
    *,
    tmp_path: Path,
    path_value: str,
    user_candidates: str,
    system_candidates: str,
) -> subprocess.CompletedProcess[str]:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)

    script = textwrap.dedent(f"""\
        __fns=$(awk 'p&&/^ps_escape\\(\\)/{{exit}} /^path_has_dir\\(\\)/{{p=1}} p{{print}}' {shlex.quote(str(INSTALL_SH))})
        if [ -z "$__fns" ]; then
            echo "resolve_install_dir block not found in install.sh" >&2
            exit 1
        fi
        eval "$__fns"
        DEFAULT_INSTALL_DIR="{fake_home}/.local/bin"
        USER_INSTALL_DIR_CANDIDATES="{user_candidates}"
        SYSTEM_INSTALL_DIR_CANDIDATES="{system_candidates}"
        INSTALL_DIR=""
        INSTALL_DIR_OVERRIDE=0
        HOME="{fake_home}"
        PATH="{path_value}"
        platform="linux"
        resolve_install_dir
        printf 'INSTALL_DIR=%s\\n' "$INSTALL_DIR"
    """)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_prefers_writable_user_path_dir(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    user_bin = fake_home / ".local" / "bin"
    user_bin.mkdir(parents=True)

    result = _run_resolution(
        tmp_path=tmp_path,
        path_value=str(user_bin),
        user_candidates=str(user_bin),
        system_candidates=str(tmp_path / "system-bin"),
    )

    assert result.returncode == 0, result.stderr
    assert f"INSTALL_DIR={user_bin}" in result.stdout


def test_uses_default_user_dir_for_unusable_system_path_dir(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    default_dir = fake_home / ".local" / "bin"

    system_parent = tmp_path / "system-parent"
    system_parent.write_text("", encoding="utf-8")
    system_bin = system_parent / "bin"

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()

    result = _run_resolution(
        tmp_path=tmp_path,
        path_value=f"{system_bin}:{tools_dir}",
        user_candidates=str(tmp_path / "user-bin"),
        system_candidates=str(system_bin),
    )

    assert result.returncode == 0, result.stderr
    assert f"INSTALL_DIR={default_dir}" in result.stdout


def test_falls_back_to_default_when_no_candidate_is_on_path(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    default_dir = fake_home / ".local" / "bin"

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()

    result = _run_resolution(
        tmp_path=tmp_path,
        path_value=str(tools_dir),
        user_candidates=str(tmp_path / "user-bin"),
        system_candidates=str(tmp_path / "system-bin"),
    )

    assert result.returncode == 0, result.stderr
    assert f"INSTALL_DIR={default_dir}" in result.stdout


def test_install_sh_never_prompts_for_sudo() -> None:
    """Installs stay sudo-free; the optional same-shell link may only use ``sudo -n``."""
    code = "\n".join(
        line for line in INSTALL_SH.read_text().splitlines() if not line.lstrip().startswith("#")
    )

    assert "sudo -v" not in code
    assert 'sudo "$@"' not in code
    assert re.findall(r"\bsudo\s+(?!-n\b)[-\w]", code) == []
