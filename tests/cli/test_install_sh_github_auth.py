"""install.sh must authenticate GitHub API metadata calls when a token is set.

These tests run the real ``download_text`` function from ``install.sh`` against
a fake ``curl`` that records its argv and stdin. The token is supplied via
public env vars (``OPENSRE_GITHUB_TOKEN``, ``GITHUB_TOKEN``, ``GH_TOKEN``) so
the lookup precedence in install.sh is exercised end-to-end — the previous
closed PRs (#6182, #6246) were caught by Greptile because tests bypassed the
lookup and set ``GITHUB_API_TOKEN`` directly.

Reference: installer-canary.yml runs on shared macOS-arm64 GitHub Actions
runners whose IP pool exhausts GitHub's anonymous 60 req/hr API limit,
causing 403s on every release. Authenticated calls lift that.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from config.constants import (
    GH_TOKEN_ENV,
    GITHUB_TOKEN_ENV,
    OPENSRE_GITHUB_TOKEN_ENV,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason=("install.sh is POSIX-only; the Windows runner has no usable bash. See issue #1099."),
)

INSTALL_SH = Path(__file__).parents[2] / "install.sh"
API_URL = "https://api.github.com/repos/Tracer-Cloud/opensre/releases/latest"


def _extract_block(start_marker: str, end_marker: str) -> str:
    """Extract a named block from install.sh, preserving line boundaries."""
    lines = INSTALL_SH.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(start_marker))
    end = next(i for i, line in enumerate(lines[start + 1 :], start + 1) if line == end_marker)
    return "\n".join(lines[start : end + 1])


def _extract_function(name: str) -> str:
    """Extract a top-level ``name() { ... }`` function from install.sh."""
    lines = INSTALL_SH.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{name}() {{"))
    depth = 0
    for i in range(start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth == 0 and i > start:
            return "\n".join(lines[start : i + 1])
    raise AssertionError(f"Could not find closing brace for {name}")


def _fake_curl(dir_path: Path) -> Path:
    """Write a curl stub that records argv (one arg per line) and stdin to files."""
    argv_log = dir_path / "argv.log"
    stdin_log = dir_path / "stdin.log"
    script = dir_path / "curl"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -eu
            : >> "{argv_log}"
            for arg in "$@"; do
              printf '%s\\n' "$arg" >> "{argv_log}"
            done
            cat > "{stdin_log}"
            exit 0
            """
        ),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return dir_path


def _run_download_text(tmp_path: Path, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Source just enough of install.sh for download_text to bind, then call it.

    Sourcing the whole script fires its own main entry point (which would
    actually hit GitHub); we extract CURL_FLAGS + download_text + the
    GITHUB_API_TOKEN lookup so the env-var precedence is exercised exactly
    as a real install would, without any of the install-time side effects.
    """
    fake_dir = tmp_path / "fakebin"
    fake_dir.mkdir()
    _fake_curl(fake_dir)

    download_text_block = _extract_function("download_text")
    curl_flags_block = _extract_block("CURL_FLAGS=(", ")").rstrip()

    block = (
        'REPO="${OPENSRE_INSTALL_REPO:-Tracer-Cloud/opensre}"\n'
        'MAIN_RELEASE_TAG="${OPENSRE_MAIN_RELEASE_TAG:-main-build}"\n'
        'GITHUB_API_TOKEN="${OPENSRE_GITHUB_TOKEN:-${GITHUB_TOKEN:-${GH_TOKEN:-}}}"\n'
        f"{curl_flags_block}\n"
        f"{download_text_block}\n"
        f'download_text "{API_URL}"\n'
    )

    full_env = {
        "PATH": f"{fake_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path / "home"),
        "OPENSRE_INSTALL_VERBOSE": "0",
        "OPENSRE_AUTO_LAUNCH": "0",
        "OPENSRE_SKIP_GH_INSTALL": "1",
        "TERM": "dumb",
    }
    full_env.update(env)

    return subprocess.run(
        ["bash", "-c", block],
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
        timeout=30,
    )


def _read_log(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_opensre_github_token_authenticates_via_stdin_not_argv(
    tmp_path: Path,
) -> None:
    """OPENSRE_GITHUB_TOKEN takes top precedence and reaches curl via stdin only."""
    result = _run_download_text(tmp_path, env={OPENSRE_GITHUB_TOKEN_ENV: "opensre-token-value"})

    assert result.returncode == 0, result.stderr

    argv_log = _read_log(tmp_path / "fakebin" / "argv.log")
    stdin_log = _read_log(tmp_path / "fakebin" / "stdin.log")

    assert API_URL in argv_log
    assert "Authorization: token opensre-token-value" in stdin_log
    # Token must never appear in argv (other users on a shared host read ps).
    assert "opensre-token-value" not in argv_log
    assert "Authorization" not in argv_log


def test_github_token_is_fallback_when_opensre_github_token_absent(
    tmp_path: Path,
) -> None:
    result = _run_download_text(tmp_path, env={GITHUB_TOKEN_ENV: "gha-token-value"})

    assert result.returncode == 0, result.stderr

    argv_log = _read_log(tmp_path / "fakebin" / "argv.log")
    stdin_log = _read_log(tmp_path / "fakebin" / "stdin.log")

    assert "Authorization: token gha-token-value" in stdin_log
    assert "gha-token-value" not in argv_log


def test_gh_token_is_fallback_when_github_token_also_absent(
    tmp_path: Path,
) -> None:
    result = _run_download_text(tmp_path, env={GH_TOKEN_ENV: "gh-cli-token-value"})

    assert result.returncode == 0, result.stderr

    argv_log = _read_log(tmp_path / "fakebin" / "argv.log")
    stdin_log = _read_log(tmp_path / "fakebin" / "stdin.log")

    assert "Authorization: token gh-cli-token-value" in stdin_log
    assert "gh-cli-token-value" not in argv_log


def test_opensre_github_token_wins_over_github_token_and_gh_token(
    tmp_path: Path,
) -> None:
    """All three set — OPENSRE_GITHUB_TOKEN takes precedence."""
    result = _run_download_text(
        tmp_path,
        env={
            OPENSRE_GITHUB_TOKEN_ENV: "wins",
            GITHUB_TOKEN_ENV: "loses-middle",
            GH_TOKEN_ENV: "loses-last",
        },
    )

    assert result.returncode == 0, result.stderr

    stdin_log = _read_log(tmp_path / "fakebin" / "stdin.log")

    assert "Authorization: token wins" in stdin_log
    assert "loses-middle" not in stdin_log
    assert "loses-last" not in stdin_log


def test_github_token_wins_over_gh_token(
    tmp_path: Path,
) -> None:
    result = _run_download_text(
        tmp_path,
        env={
            GITHUB_TOKEN_ENV: "wins-middle",
            GH_TOKEN_ENV: "loses-last",
        },
    )

    assert result.returncode == 0, result.stderr

    stdin_log = _read_log(tmp_path / "fakebin" / "stdin.log")

    assert "Authorization: token wins-middle" in stdin_log
    assert "loses-last" not in stdin_log


def test_no_authorization_header_without_any_token(tmp_path: Path) -> None:
    """Anonymous callers stay anonymous — no header leakage in either path."""
    result = _run_download_text(tmp_path, env={})

    assert result.returncode == 0, result.stderr

    argv_log = _read_log(tmp_path / "fakebin" / "argv.log")
    stdin_log = _read_log(tmp_path / "fakebin" / "stdin.log")

    assert "Authorization" not in argv_log
    assert "Authorization" not in stdin_log
    assert API_URL in argv_log
    # Accept + User-Agent still applied even without a token.
    assert "-H" in argv_log


def test_download_to_does_not_add_authorization(tmp_path: Path) -> None:
    """Asset downloads (download_to) must stay anonymous even when a token is set.

    Asset + checksum downloads go through corp proxies that may rewrite or
    reject authenticated requests; the release CDN serves binaries
    anonymously. This pins the bash side's behavior so a future refactor
    cannot accidentally route downloads through the same auth path as
    metadata lookups.
    """
    fake_dir = tmp_path / "fakebin"
    fake_dir.mkdir()
    _fake_curl(fake_dir)

    download_to_block = _extract_function("download_to")

    full_env = {
        "PATH": f"{fake_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path / "home"),
        OPENSRE_GITHUB_TOKEN_ENV: "would-be-mistake",
        GITHUB_TOKEN_ENV: "would-be-mistake",
        GH_TOKEN_ENV: "would-be-mistake",
    }

    block = (
        'REPO="${OPENSRE_INSTALL_REPO:-Tracer-Cloud/opensre}"\n'
        'GITHUB_API_TOKEN="${OPENSRE_GITHUB_TOKEN:-${GITHUB_TOKEN:-${GH_TOKEN:-}}}"\n'
        f"{download_to_block}\n"
        f'download_to "{API_URL}" "{tmp_path / "asset.bin"}"\n'
    )

    result = subprocess.run(
        ["bash", "-c", block],
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr

    argv_log = _read_log(tmp_path / "fakebin" / "argv.log")
    stdin_log = _read_log(tmp_path / "fakebin" / "stdin.log")

    assert "Authorization" not in argv_log
    assert "Authorization" not in stdin_log
    assert "would-be-mistake" not in argv_log
    assert "would-be-mistake" not in stdin_log
