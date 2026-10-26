"""Narrow GitHub CLI access for CI inspection."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

from integrations.github.client import resolve_github_token
from integrations.github.tools.ci_fix.errors import (
    ERR_GH_UNAVAILABLE,
    ERR_GITHUB_TOKEN,
    ERR_PR_NOT_FOUND,
    GitHubCiFixError,
)

DEFAULT_GH_TIMEOUT_SECONDS = 120


def run_gh_json(
    args: list[str],
    *,
    repo: str,
    github_token: str | None,
    timeout: int = DEFAULT_GH_TIMEOUT_SECONDS,
    repo_flag: bool = True,
) -> dict[str, Any]:
    """Run a read-only ``gh`` command and decode a JSON object."""
    raw = run_gh_text(
        args, repo=repo, github_token=github_token, timeout=timeout, repo_flag=repo_flag
    )
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise GitHubCiFixError(
            ERR_GH_UNAVAILABLE,
            "GitHub CLI returned invalid JSON while inspecting CI; no push was made.",
        ) from exc
    if not isinstance(parsed, dict):
        raise GitHubCiFixError(
            ERR_GH_UNAVAILABLE,
            "GitHub CLI returned an unexpected response while inspecting CI; no push was made.",
        )
    return parsed


def run_gh_text(
    args: list[str],
    *,
    repo: str,
    github_token: str | None,
    timeout: int = DEFAULT_GH_TIMEOUT_SECONDS,
    repo_flag: bool = True,
    cwd: str | None = None,
) -> str:
    """Run a narrow ``gh`` command with OpenSRE-resolved token auth.

    ``repo_flag=False`` omits the global ``-R`` selector for subcommands that
    reject it (``gh api``, whose endpoint path already names the repo). *cwd*
    runs the command inside a checkout, for subcommands that act on one.
    """
    token = resolve_github_token(github_token)
    if not token:
        raise GitHubCiFixError(
            ERR_GITHUB_TOKEN,
            "A GitHub token is required to inspect CI and push fixes; no push was made.",
        )
    if shutil.which("gh") is None:
        raise GitHubCiFixError(
            ERR_GH_UNAVAILABLE,
            "GitHub CLI is required to inspect CI logs; no push was made.",
        )

    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env["GITHUB_TOKEN"] = token
    env.pop("GH_ENTERPRISE_TOKEN", None)
    argv = ["gh", "-R", repo, *args] if repo_flag else ["gh", *args]
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=max(1, timeout),
            env=env,
            check=False,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitHubCiFixError(
            ERR_GH_UNAVAILABLE,
            f"GitHub CLI timed out while inspecting CI after {timeout}s; no push was made.",
        ) from exc
    except OSError as exc:
        raise GitHubCiFixError(
            ERR_GH_UNAVAILABLE,
            f"GitHub CLI failed while inspecting CI: {exc}; no push was made.",
        ) from exc

    stdout = _redact(completed.stdout or "", token)
    stderr = _redact(completed.stderr or "", token)
    if completed.returncode == 0:
        return stdout
    message = stderr.strip() or stdout.strip() or f"gh exited with {completed.returncode}"
    kind = ERR_PR_NOT_FOUND if "not found" in message.lower() else ERR_GH_UNAVAILABLE
    raise GitHubCiFixError(kind, f"{message}; no push was made.")


def _redact(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


__all__ = ["DEFAULT_GH_TIMEOUT_SECONDS", "run_gh_json", "run_gh_text"]
