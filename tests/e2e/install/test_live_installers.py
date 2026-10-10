"""Live installer e2e: real CDN / GitHub / Homebrew (no curl shim).

Ignored by ``ci.yml`` (PR/push gate) and skipped by default pytest
(``norecursedirs = tests/e2e``). Opt-in locally, and run post-publish by
``.github/workflows/installer-canary.yml``:

    OPENSRE_LIVE_INSTALL=1 uv run pytest tests/e2e/install/ -q
    ./trace smoke --suite all --only 'install.live_*'

Offline install coverage stays in CI via ``tests/cli/test_install_matrix.py``.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from config.constants.github import GITHUB_TOKEN_ENV
from config.constants.paths import REPO_ROOT
from tests.e2e.install._shared import assert_binary_smoke, assert_checksum_verified

# Keep the canary token out of the test process; pass it only to install.sh.
_CANARY_INSTALL_GITHUB_TOKEN = os.environ.pop(GITHUB_TOKEN_ENV, None)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.live_install,
    pytest.mark.skipif(
        os.environ.get("OPENSRE_LIVE_INSTALL") != "1",
        reason="Set OPENSRE_LIVE_INSTALL=1 to run live installer e2e",
    ),
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX live installers; Windows uses install.ps1 (separate host)",
    ),
]

INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_CDN = "https://install.opensre.com"


def test_live_cdn_serves_install_sh() -> None:
    """``curl -fsSL https://install.opensre.com`` returns the bash installer."""
    result = subprocess.run(
        ["curl", "-fsSL", INSTALL_CDN],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    body = result.stdout
    assert "#!/usr/bin/env bash" in body or "INSTALL_CHANNEL" in body
    assert "main()" in body or "finish_install" in body
    assert "opensre" in body.lower()


def _sanitized_install_env(home: Path, *, extra_path: str = "") -> dict[str, str]:
    """PATH without repo ``.venv/bin`` so ensure_on_path cannot clobber editable installs."""
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["OPENSRE_AUTO_LAUNCH"] = "0"
    env["OPENSRE_SKIP_GH_INSTALL"] = "1"
    env["OPENSRE_INSTALL_VERBOSE"] = "1"
    env["TERM"] = "dumb"
    path_parts = [
        p
        for p in env.get("PATH", "").split(os.pathsep)
        if p and ".venv/bin" not in p and "/venv/bin" not in p and "site-packages" not in p
    ]
    if extra_path:
        path_parts.insert(0, extra_path)
    # Keep a minimal system PATH so curl/tar/uname still resolve.
    for system_bin in ("/usr/bin", "/bin", "/usr/sbin", "/sbin", "/opt/homebrew/bin"):
        if system_bin not in path_parts and Path(system_bin).is_dir():
            path_parts.append(system_bin)
    env["PATH"] = os.pathsep.join(path_parts)
    return env


def test_live_install_sh_main_to_temp_dir(tmp_path: Path) -> None:
    """Real ``bash install.sh --main`` against GitHub into an isolated bin dir."""
    install_dir = tmp_path / "bin"
    install_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    venv_opensre = REPO_ROOT / ".venv" / "bin" / "opensre"
    venv_before = venv_opensre.resolve() if venv_opensre.exists() else None
    env = _sanitized_install_env(home)
    if _CANARY_INSTALL_GITHUB_TOKEN:
        env[GITHUB_TOKEN_ENV] = _CANARY_INSTALL_GITHUB_TOKEN

    result = subprocess.run(
        [
            "bash",
            str(INSTALL_SH),
            "--main",
            "--install-dir",
            str(install_dir),
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
    binary = install_dir / "opensre"
    assert binary.is_file(), combined
    assert bool(binary.stat().st_mode & stat.S_IXUSR)
    version = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert version.returncode == 0, version.stderr
    assert "opensre" in version.stdout.lower() or "0." in version.stdout
    if venv_before is not None:
        assert venv_opensre.exists(), "live install must not remove .venv/bin/opensre"
        assert venv_opensre.resolve() == venv_before, (
            "live install must not replace the editable .venv/bin/opensre console script"
        )


def test_live_install_sh_release_channel(tmp_path: Path) -> None:
    """Real ``bash install.sh --release`` (checksum-verified) then ``--help`` / ``_package-smoke``.

    ``OPENSRE_LIVE_INSTALL_TAG`` pins a specific release tag (set by the
    installer-canary workflow right after a release publishes); unset, the
    installer resolves the latest release itself, exercising GitHub release
    resolution the way a real user's first install does.
    """
    install_dir = tmp_path / "bin"
    install_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    env = _sanitized_install_env(home)

    installer_args = ["--release", "--install-dir", str(install_dir)]
    requested_tag = os.environ.get("OPENSRE_LIVE_INSTALL_TAG", "").strip()
    if requested_tag:
        installer_args += ["--version", requested_tag.removeprefix("v")]

    result = subprocess.run(
        [
            "bash",
            "-o",
            "pipefail",
            "-c",
            f'curl -fsSL {INSTALL_CDN} | bash -s -- "$@"',
            "opensre-installer",
            *installer_args,
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

    binary = install_dir / "opensre"
    assert binary.is_file(), combined
    assert bool(binary.stat().st_mode & stat.S_IXUSR)

    assert_binary_smoke(binary, help_flag="--help", requested_tag=requested_tag)


@pytest.mark.skipif(shutil.which("brew") is None, reason="Homebrew not installed")
def test_live_brew_tap_and_fetch() -> None:
    """Live Homebrew: ensure tap exists and the bottle/source can be fetched."""
    tap = subprocess.run(
        ["brew", "tap", "tracer-cloud/tap"],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert tap.returncode == 0, tap.stderr + tap.stdout

    info = subprocess.run(
        ["brew", "info", "--json=v2", "tracer-cloud/tap/opensre"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert info.returncode == 0, info.stderr
    assert "opensre" in info.stdout

    fetch = subprocess.run(
        ["brew", "fetch", "--force", "tracer-cloud/tap/opensre"],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert fetch.returncode == 0, fetch.stderr + fetch.stdout


@pytest.mark.skipif(shutil.which("brew") is None, reason="Homebrew not installed")
def test_live_brew_install_and_version() -> None:
    """Full ``brew install`` with onedir-aware formula → --version → uninstall."""
    listed = subprocess.run(
        ["brew", "list", "--versions", "opensre"],
        capture_output=True,
        text=True,
        check=False,
    )
    was_installed = listed.returncode == 0 and bool(listed.stdout.strip())

    subprocess.run(
        ["brew", "tap", "tracer-cloud/tap"],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    subprocess.run(
        ["brew", "trust", "--formula", "tracer-cloud/tap/opensre"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    # Stage local formula (libexec + _internal) into the live tap checkout.
    local_formula = REPO_ROOT.parent / "homebrew-tap" / "Formula" / "opensre.rb"
    tap_repo = subprocess.run(
        ["brew", "--repo", "tracer-cloud/tap"],
        capture_output=True,
        text=True,
        check=False,
    )
    tap_formula = Path(tap_repo.stdout.strip()) / "Formula" / "opensre.rb"
    backup: str | None = None
    if local_formula.is_file() and tap_formula.is_file():
        backup = tap_formula.read_text(encoding="utf-8")
        tap_formula.write_text(local_formula.read_text(encoding="utf-8"), encoding="utf-8")

    install = subprocess.run(
        ["brew", "install", "tracer-cloud/tap/opensre"],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    try:
        assert install.returncode == 0, install.stderr + install.stdout

        which = subprocess.run(
            ["brew", "--prefix", "opensre"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert which.returncode == 0, which.stderr
        prefix = Path(which.stdout.strip())
        binary = prefix / "bin" / "opensre"
        if not binary.is_file():
            resolved = shutil.which("opensre")
            assert resolved, "brew install succeeded but opensre not on PATH"
            binary = Path(resolved)

        resolved_bin = binary.resolve()
        internal_candidates = (
            resolved_bin.parent / "_internal",
            prefix / "libexec" / "_internal",
            prefix / "libexec" / "opensre" / "_internal",
        )
        assert any(p.is_dir() for p in internal_candidates), (
            "brew formula installed a broken PyInstaller layout: missing _internal. "
            f"looked in {[str(p) for p in internal_candidates]}"
        )

        version = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert version.returncode == 0, version.stderr
        assert "opensre" in version.stdout.lower() or "0." in version.stdout
    finally:
        if not was_installed:
            subprocess.run(
                ["brew", "uninstall", "--force", "opensre"],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        if backup is not None and tap_formula.is_file():
            tap_formula.write_text(backup, encoding="utf-8")
