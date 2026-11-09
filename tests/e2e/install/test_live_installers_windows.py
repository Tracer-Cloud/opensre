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
from pathlib import Path

import pytest

from config.constants.github import OPENSRE_INSTALL_GITHUB_TOKEN_ENV
from tests.e2e.install._shared import assert_binary_smoke, assert_checksum_verified

_CANARY_GITHUB_TOKEN = os.environ.pop(OPENSRE_INSTALL_GITHUB_TOKEN_ENV, None)

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


def _sanitized_install_env(
    install_dir: Path, *, channel: str, requested_tag: str = ""
) -> dict[str, str]:
    env = os.environ.copy()
    if _CANARY_GITHUB_TOKEN:
        env[OPENSRE_INSTALL_GITHUB_TOKEN_ENV] = _CANARY_GITHUB_TOKEN
    env["OPENSRE_INSTALL_DIR"] = str(install_dir)
    env["OPENSRE_INSTALL_CHANNEL"] = channel
    env["OPENSRE_AUTO_LAUNCH"] = "0"
    env["OPENSRE_SKIP_GH_INSTALL"] = "1"
    env["OPENSRE_INSTALL_VERBOSE"] = "1"
    if requested_tag:
        env["OPENSRE_VERSION"] = requested_tag.removeprefix("v")
    else:
        env.pop("OPENSRE_VERSION", None)
    return env


def test_live_install_ps1_release_channel(tmp_path: Path) -> None:
    """Real ``install.ps1`` (checksum-verified) then ``-h`` / ``_package-smoke``.

    ``OPENSRE_LIVE_INSTALL_TAG`` pins a specific release tag (set by the
    installer-canary workflow right after a release publishes); unset, the
    installer resolves the latest release itself.
    """
    install_dir = tmp_path / "bin"
    install_dir.mkdir()
    requested_tag = os.environ.get("OPENSRE_LIVE_INSTALL_TAG", "").strip()
    env = _sanitized_install_env(install_dir, channel="release", requested_tag=requested_tag)

    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"Invoke-RestMethod -Uri '{INSTALL_CDN}' | Invoke-Expression",
        ],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert_checksum_verified(combined)

    binary = install_dir / "opensre.exe"
    assert binary.is_file(), combined

    assert_binary_smoke(binary, help_flag="-h", requested_tag=requested_tag)
