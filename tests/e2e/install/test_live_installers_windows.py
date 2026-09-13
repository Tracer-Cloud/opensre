"""Live installer e2e for Windows: real CDN / GitHub via ``install.ps1``.

Companion to ``test_live_installers.py`` (POSIX-only). Opt-in locally, and run
post-publish by ``.github/workflows/installer-canary.yml`` on a Windows
runner:

    $env:OPENSRE_LIVE_INSTALL = "1"; uv run pytest tests/e2e/install/test_live_installers_windows.py -q
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.e2e.install._shared import assert_binary_smoke, assert_checksum_verified

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.live_install,
    pytest.mark.skipif(
        os.environ.get("OPENSRE_LIVE_INSTALL") != "1",
        reason="Set OPENSRE_LIVE_INSTALL=1 to run live installer e2e",
    ),
    pytest.mark.skipif(sys.platform != "win32", reason="install.ps1 only runs on Windows"),
]

INSTALL_CDN = "https://install.opensre.com"
_LAYOUT_MARKER = "layout-v1.marker"
_CURRENT_POINTER = "current.txt"
_INSTALL_LOCK = ".opensre-app.install.lock"
_CLEANUP_WORKER = Path("_internal/surfaces/cli/lifecycle/windows/uninstall_cleanup.ps1")


def _windows_powershell() -> Path:
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    executable = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    assert executable.is_file(), f"Windows PowerShell 5.1 was not found at {executable}"
    return executable


def _sanitized_install_env(
    install_dir: Path, *, channel: str, requested_tag: str = ""
) -> dict[str, str]:
    env = os.environ.copy()
    profile = install_dir.parent / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(profile)
    env["USERPROFILE"] = str(profile)
    env["OPENSRE_HOME"] = str(profile / ".opensre")
    env["OPENSRE_INSTALL_DIR"] = str(install_dir)
    env["OPENSRE_INSTALL_CHANNEL"] = channel
    env["OPENSRE_AUTO_LAUNCH"] = "0"
    env["OPENSRE_SKIP_GH_INSTALL"] = "1"
    env["OPENSRE_INSTALL_VERBOSE"] = "1"
    env["OPENSRE_NO_TELEMETRY"] = "1"
    env["DO_NOT_TRACK"] = "1"
    if requested_tag:
        env["OPENSRE_VERSION"] = requested_tag.removeprefix("v")
    else:
        env.pop("OPENSRE_VERSION", None)
    return env


def _run_installer(env: dict[str, str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    command_env = env.copy()
    command_env["OPENSRE_CANARY_INSTALL_URL"] = INSTALL_CDN
    return subprocess.run(
        [
            str(_windows_powershell()),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ("Invoke-RestMethod -Uri $env:OPENSRE_CANARY_INSTALL_URL | Invoke-Expression"),
        ],
        cwd=cwd,
        env=command_env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_launcher(
    launcher: Path,
    *args: str,
    env: dict[str, str],
    timeout: float = 300,
) -> subprocess.CompletedProcess[str]:
    command_env = env.copy()
    command_env["OPENSRE_CANARY_LAUNCHER"] = str(launcher)
    arguments = " ".join(_powershell_literal(arg) for arg in args)
    return subprocess.run(
        [
            str(_windows_powershell()),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"& $env:OPENSRE_CANARY_LAUNCHER {arguments}; exit $LASTEXITCODE",
        ],
        cwd=launcher.parent,
        env=command_env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _assert_launcher_smoke(launcher: Path, *, requested_tag: str, env: dict[str, str]) -> None:
    for args in (("--version",), ("-h",), ("_package-smoke",)):
        result = _run_launcher(launcher, *args, env=env, timeout=60)
        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        if args == ("--version",) and requested_tag:
            assert requested_tag.removeprefix("v") in result.stdout, output


def _active_bundle(install_dir: Path) -> tuple[str, Path]:
    app_dir = install_dir / ".opensre-app"
    marker = app_dir / _LAYOUT_MARKER
    current = app_dir / _CURRENT_POINTER
    assert marker.is_file(), f"managed install is missing ownership marker {marker}"
    assert current.is_file(), f"managed install is missing current pointer {current}"

    install_id = current.read_text(encoding="utf-8").strip()
    assert install_id
    assert Path(install_id).name == install_id
    bundle = app_dir / "versions" / install_id
    assert (bundle / "opensre.exe").is_file()
    assert (bundle / "_internal").is_dir()
    assert (bundle / _CLEANUP_WORKER).is_file()
    return install_id, bundle


def _wait_until_removed(*paths: Path, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while any(path.exists() for path in paths) and time.monotonic() < deadline:
        time.sleep(0.1)
    remaining = [str(path) for path in paths if path.exists()]
    assert not remaining, f"timed out waiting for detached uninstall cleanup: {remaining}"


def test_live_install_ps1_release_update_and_uninstall(tmp_path: Path) -> None:
    """Exercise the published managed Windows lifecycle in a disposable profile.

    ``OPENSRE_LIVE_INSTALL_TAG`` pins a specific release tag (set by the
    installer-canary workflow right after a release publishes); unset, the
    installer resolves the latest release itself.
    """
    install_dir = tmp_path / "OpenSRE lifecycle canary" / "bin"
    install_dir.mkdir(parents=True)
    requested_tag = os.environ.get("OPENSRE_LIVE_INSTALL_TAG", "").strip()
    env = _sanitized_install_env(install_dir, channel="release", requested_tag=requested_tag)

    install = _run_installer(env, cwd=tmp_path)
    install_output = install.stdout + install.stderr
    assert install.returncode == 0, install_output
    assert_checksum_verified(install_output)

    launcher = install_dir / "opensre.cmd"
    assert launcher.is_file(), install_output
    first_install_id, first_bundle = _active_bundle(install_dir)
    assert_binary_smoke(
        first_bundle / "opensre.exe",
        help_flag="-h",
        requested_tag=requested_tag,
        cwd=install_dir,
        env=env,
    )
    _assert_launcher_smoke(launcher, requested_tag=requested_tag, env=env)

    update = _run_launcher(launcher, "update", "--yes", env=env, timeout=600)
    update_output = update.stdout + update.stderr
    assert update.returncode == 0, update_output

    second_install_id, second_bundle = _active_bundle(install_dir)
    if "already up to date" in update_output.casefold():
        assert second_install_id == first_install_id
        assert second_bundle == first_bundle
    else:
        assert "updated:" in update_output.casefold(), update_output
        assert_checksum_verified(update_output)
        assert second_install_id != first_install_id
    assert_binary_smoke(
        second_bundle / "opensre.exe",
        help_flag="-h",
        requested_tag="",
        cwd=install_dir,
        env=env,
    )
    _assert_launcher_smoke(launcher, requested_tag="", env=env)

    unrelated = install_dir / "keep-me.txt"
    unrelated.write_text("unrelated", encoding="utf-8")
    uninstall = _run_launcher(launcher, "uninstall", "--yes", env=env, timeout=300)
    uninstall_output = uninstall.stdout + uninstall.stderr
    assert uninstall.returncode == 0, uninstall_output

    app_dir = install_dir / ".opensre-app"
    _wait_until_removed(launcher, app_dir, install_dir / _INSTALL_LOCK)
    assert unrelated.read_text(encoding="utf-8") == "unrelated"
