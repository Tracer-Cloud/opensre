"""install.sh must authenticate GitHub API metadata calls when a token is available."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from config.constants import GH_TOKEN_ENV, GITHUB_TOKEN_ENV

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
API_URL = "https://api.github.com/repos/Tracer-Cloud/opensre/releases/latest"


def _extract_function(name: str) -> str:
    lines = INSTALL_SH.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"{name}() {{")
    end = lines.index("}", start)
    return "\n".join(lines[start : end + 1])


def _run_download_text(*, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    script = textwrap.dedent(
        f"""\
        CURL_FLAGS=(--silent)
        curl() {{
          while [ "$#" -gt 0 ]; do
            case "$1" in
              --config)
                printf 'CONFIG_FILE=%s\\n' "$2"
                cat "$2"
                shift 2
                ;;
              *)
                printf 'ARG=%s\\n' "$1"
                shift
                ;;
            esac
          done
        }}
        {_extract_function("download_text")}
        download_text "{API_URL}"
        """
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), **env},
    )


def test_github_token_adds_bearer_authorization() -> None:
    result = _run_download_text(env={GITHUB_TOKEN_ENV: "test-token"})

    assert result.returncode == 0, result.stderr
    assert 'header = "Authorization: Bearer test-token"' in result.stdout
    assert API_URL in result.stdout
    # The token must not travel through curl's process arguments.
    assert "ARG=Authorization" not in result.stdout


def test_gh_token_is_the_fallback_when_github_token_is_absent() -> None:
    result = _run_download_text(env={GH_TOKEN_ENV: "fallback-token"})

    assert result.returncode == 0, result.stderr
    assert 'header = "Authorization: Bearer fallback-token"' in result.stdout


def test_no_authorization_without_a_token() -> None:
    result = _run_download_text(env={})

    assert result.returncode == 0, result.stderr
    assert "Authorization" not in result.stdout
    assert "CONFIG_FILE" not in result.stdout
