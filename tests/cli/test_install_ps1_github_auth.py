"""install.ps1 must send a GitHub token to API requests only, never downloads."""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from config.constants import GH_TOKEN_ENV, GITHUB_TOKEN_ENV

INSTALL_PS1 = Path(__file__).parents[2] / "install.ps1"


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def test_install_ps1_uses_token_headers_only_for_api_requests() -> None:
    source = INSTALL_PS1.read_text(encoding="utf-8")

    assert "function Get-OpenSreApiHeaders" in source
    assert "Headers = Get-OpenSreApiHeaders" in source
    # Download paths stay unauthenticated: release assets redirect to signed
    # URLs on another host, which must never receive the token.
    assert "Headers = Get-OpenSreRequestHeaders" in source


def test_install_ps1_api_headers_include_bearer_token() -> None:
    shell = _powershell()
    if shell is None:
        pytest.skip("PowerShell is not installed in this environment.")

    script_path = str(INSTALL_PS1).replace("'", "''")
    script = textwrap.dedent(
        f"""
        Remove-Item "Env:{GITHUB_TOKEN_ENV}" -ErrorAction SilentlyContinue
        $env:{GH_TOKEN_ENV} = 'fallback-token'
        . '{script_path}' -SkipMain
        $fallback = Get-OpenSreApiHeaders
        Write-Output "FALLBACK=$($fallback['Authorization'])"
        $env:{GITHUB_TOKEN_ENV} = 'primary-token'
        $primary = Get-OpenSreApiHeaders
        Write-Output "PRIMARY=$($primary['Authorization'])"
        $download = Get-OpenSreRequestHeaders
        Write-Output "DOWNLOAD_HAS_AUTH=$($download.ContainsKey('Authorization'))"
        """
    )

    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    assert "FALLBACK=Bearer fallback-token" in output
    assert "PRIMARY=Bearer primary-token" in output
    assert "DOWNLOAD_HAS_AUTH=False" in output
