"""install.ps1 must authenticate GitHub API metadata calls when a token is set.

Reference: same rationale as ``test_install_sh_github_auth.py``. The Windows
canary leg never hits the 60 req/hr anonymous limit (different IP pool than
macOS-arm64), so this test exercises the PowerShell installer directly to
keep parity with the bash side.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

from config.constants import (
    GH_TOKEN_ENV,
    GITHUB_TOKEN_ENV,
    OPENSRE_GITHUB_TOKEN_ENV,
)

INSTALL_PS1 = Path(__file__).parents[2] / "install.ps1"


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


pytestmark = pytest.mark.skipif(
    _powershell() is None,
    reason="PowerShell (pwsh or powershell) is not installed on this host.",
)


def _fake_curl(dir_path: Path) -> Path:
    """Unused by ps1 tests but kept for shape parity with the bash test."""
    script = dir_path / "curl"
    script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return dir_path


def _run_get_headers(tmp_path: Path, *, env: dict[str, str]) -> dict[str, str]:
    """Dot-source install.ps1 and call Get-OpenSreRequestHeaders.

    The function is the single chokepoint every GitHub API metadata call goes
    through; if it returns the right dict, every downstream call inherits the
    correct headers. Install.ps1 has no public main that fires on dot-source,
    so just invoking the function exercises the env-var lookup the same way
    a real install would.
    """
    fake_dir = tmp_path / "fakebin"
    fake_dir.mkdir()
    _fake_curl(fake_dir)

    full_env = {
        "PATH": f"{fake_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    full_env.update(env)

    script = textwrap.dedent(
        f"""\
        . {shlex.quote(str(INSTALL_PS1))} -SkipMain
        $h = Get-OpenSreRequestHeaders
        $h | ConvertTo-Json -Compress
        """
    )

    shell = _powershell()
    assert shell is not None
    result = subprocess.run(
        [shell, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        env=full_env,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_opensre_github_token_authenticates(tmp_path: Path) -> None:
    headers = _run_get_headers(tmp_path, env={OPENSRE_GITHUB_TOKEN_ENV: "opensre-token-value"})
    assert headers["Authorization"] == "token opensre-token-value"


def test_github_token_is_fallback_when_opensre_github_token_absent(tmp_path: Path) -> None:
    headers = _run_get_headers(tmp_path, env={GITHUB_TOKEN_ENV: "gha-token-value"})
    assert headers["Authorization"] == "token gha-token-value"


def test_gh_token_is_fallback_when_both_higher_precedence_absent(tmp_path: Path) -> None:
    headers = _run_get_headers(tmp_path, env={GH_TOKEN_ENV: "gh-cli-token-value"})
    assert headers["Authorization"] == "token gh-cli-token-value"


def test_opensre_github_token_wins_over_github_token_and_gh_token(tmp_path: Path) -> None:
    headers = _run_get_headers(
        tmp_path,
        env={
            OPENSRE_GITHUB_TOKEN_ENV: "wins",
            GITHUB_TOKEN_ENV: "loses-middle",
            GH_TOKEN_ENV: "loses-last",
        },
    )
    assert headers["Authorization"] == "token wins"


def test_github_token_wins_over_gh_token(tmp_path: Path) -> None:
    headers = _run_get_headers(
        tmp_path,
        env={
            GITHUB_TOKEN_ENV: "wins-middle",
            GH_TOKEN_ENV: "loses-last",
        },
    )
    assert headers["Authorization"] == "token wins-middle"


def test_no_authorization_header_without_any_token(tmp_path: Path) -> None:
    headers = _run_get_headers(tmp_path, env={})
    assert "Authorization" not in headers
