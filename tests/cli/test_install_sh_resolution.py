"""Tests for install directory resolution in install.sh."""

from __future__ import annotations

import os
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

# ``os.geteuid`` does not exist on Windows. The skipif decorator below is
# evaluated at decorator-application time (i.e. module import), so a bare
# ``os.geteuid() == 0`` check would raise ``AttributeError`` on Windows
# *before* ``pytestmark`` ever takes effect. ``hasattr`` short-circuits the
# ``and`` so the ``os.geteuid()`` call only runs on platforms that have it.
_RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

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
        __fns=$(awk 'p&&/^ps_escape\\(\\)/{{exit}} /^path_has_dir\\(\\)/{{p=1}} p{{print}}' {INSTALL_SH})
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
        INSTALL_WITH_SUDO=0
        HOME="{fake_home}"
        PATH="{path_value}"
        platform="linux"
        resolve_install_dir
        printf 'INSTALL_DIR=%s\\n' "$INSTALL_DIR"
        printf 'INSTALL_WITH_SUDO=%s\\n' "$INSTALL_WITH_SUDO"
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
    assert "INSTALL_WITH_SUDO=0" in result.stdout


@pytest.mark.skipif(_RUNNING_AS_ROOT, reason="sudo fallback is only meaningful for non-root users")
def test_uses_sudo_for_non_writable_system_path_dir(tmp_path: Path) -> None:
    system_bin = tmp_path / "system-bin"
    system_bin.mkdir()
    system_bin.chmod(0o555)

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    fake_sudo = tools_dir / "sudo"
    fake_sudo.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    fake_sudo.chmod(0o755)

    result = _run_resolution(
        tmp_path=tmp_path,
        path_value=f"{system_bin}:{tools_dir}",
        user_candidates=str(tmp_path / "user-bin"),
        system_candidates=str(system_bin),
    )

    assert result.returncode == 0, result.stderr
    assert f"INSTALL_DIR={system_bin}" in result.stdout
    assert "INSTALL_WITH_SUDO=1" in result.stdout


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
    assert "INSTALL_WITH_SUDO=0" in result.stdout
