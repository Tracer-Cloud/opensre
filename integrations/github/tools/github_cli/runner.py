"""Run authenticated ``gh`` subprocesses for the github_cli tools."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

from integrations.github.tools.github_cli.credentials import resolve_github_token

DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 120

# ``gh api`` list endpoints answer with the whole object graph: 30 workflow runs
# is ~382k characters against a busy repo. Uncapped, that lands in the message
# and the context budget silently trims the tail, so the agent reads a handful
# of records and cannot tell the rest existed. Cap here instead and say so on
# the payload, matching what ``shell_run`` already does.
MAX_GH_OUTPUT_CHARS = 6_000

# Top-level ``gh`` commands that must never run under OpenSRE-injected credentials.
# - auth: ``gh auth token`` prints GH_TOKEN to stdout (self-exfiltration)
# - extension: install/run can download and execute arbitrary code
# - secret: mutate repository secrets
# - codespace / ssh-key / gpg-key / config: credential and host-config mutation surface
_DENIED_TOP_LEVEL_COMMANDS = frozenset(
    {
        "auth",
        "extension",
        "secret",
        "codespace",
        "ssh-key",
        "gpg-key",
        "config",
    }
)

# Mutating ``gh run`` / ``gh workflow`` subcommands. ``list`` and ``view`` stay
# allowed so a /goal can look up workflow runs by SHA.
_DENIED_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "run": frozenset({"rerun", "cancel", "delete", "watch", "download"}),
    "workflow": frozenset({"run", "enable", "disable"}),
}

# Global flags that consume a following value (after the ``gh`` binary).
# Note: ``-h`` is ``--help`` (boolean), not a short form of ``--hostname``.
_VALUE_FLAGS = frozenset(
    {
        "-R",
        "--repo",
        "--hostname",
        "--jq",
        "-t",
        "--template",
    }
)


def positional_gh_tokens(args: list[str] | tuple[str, ...]) -> list[str]:
    """Return command positionals with every flag removed, wherever it sits.

    ``gh`` accepts global flags before and after the command word, so
    ``run -R owner/repo rerun 123`` must read as ``run rerun``: a flag between
    the command and its subcommand cannot hide a denied subcommand.
    """
    positionals: list[str] = []
    i = 0
    cleaned = [str(a) for a in args]
    while i < len(cleaned):
        token = cleaned[i]
        if not token:
            i += 1
            continue
        if token == "--":
            positionals.extend(cleaned[i + 1 :])
            break
        if token.startswith("-"):
            name, _, inline = token.partition("=")
            if not inline and name in _VALUE_FLAGS and i + 1 < len(cleaned):
                nxt = cleaned[i + 1]
                if nxt and not nxt.startswith("-"):
                    i += 2
                    continue
            i += 1
            continue
        positionals.append(token)
        i += 1
    return positionals


def denied_gh_command(args: list[str] | tuple[str, ...]) -> str | None:
    """Return the blocked ``gh`` command (or ``command sub``), or None if allowed."""
    positionals = positional_gh_tokens(args)
    if not positionals:
        return None
    command = positionals[0].lower()
    if command in _DENIED_TOP_LEVEL_COMMANDS:
        return command
    denied_subs = _DENIED_SUBCOMMANDS.get(command)
    if denied_subs is None:
        return None
    sub = positionals[1].lower() if len(positionals) > 1 else ""
    if sub in denied_subs:
        return f"{command} {sub}"
    return None


def build_gh_argv(*, args: list[str], repo: str | None = None) -> list[str]:
    """Build full argv for ``gh`` including optional ``-R owner/name``."""
    argv = ["gh"]
    cleaned_repo = (repo or "").strip()
    positionals = positional_gh_tokens(args)
    command = positionals[0].lower() if positionals else None
    if cleaned_repo and command != "api":
        argv.extend(["-R", cleaned_repo])
    argv.extend(str(a) for a in args)
    return argv


def _redact_secret(text: str, secret: str) -> str:
    if not secret or not text:
        return text
    return text.replace(secret, "***")


def _cap_output(text: str) -> tuple[str, bool]:
    """Return ``text`` within the output cap, and whether anything was dropped."""
    if len(text) <= MAX_GH_OUTPUT_CHARS:
        return text, False
    return text[:MAX_GH_OUTPUT_CHARS], True


def run_gh(
    *,
    args: list[str],
    repo: str | None = None,
    github_token: str | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Execute ``gh`` with OpenSRE-resolved credentials.

    Never returns the token: denied subcommands that could print or misuse it are
    rejected before spawn, and any accidental token echo in stdout/stderr is
    redacted from the returned payload.
    """
    if not args:
        return {
            "ok": False,
            "error": "args must be a non-empty list of arguments after `gh`.",
            "error_type": "validation_error",
            "argv": ["gh"],
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }

    blocked = denied_gh_command(args)
    if blocked is not None:
        return {
            "ok": False,
            "error": (
                f"`gh {blocked}` is blocked by OpenSRE (credential / host-config / "
                "extension commands are not allowed via github_cli)."
            ),
            "error_type": "policy_error",
            "argv": build_gh_argv(args=args, repo=repo),
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }

    token = resolve_github_token(github_token)
    if not token:
        return {
            "ok": False,
            "error": (
                "GitHub token is required. Configure the GitHub integration, or set "
                "GITHUB_MCP_AUTH_TOKEN, GITHUB_TOKEN, or GH_TOKEN."
            ),
            "error_type": "configuration_error",
            "argv": build_gh_argv(args=args, repo=repo),
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }

    if shutil.which("gh") is None:
        return {
            "ok": False,
            "error": "The GitHub CLI (`gh`) is not installed or not on PATH.",
            "error_type": "missing_binary",
            "argv": build_gh_argv(args=args, repo=repo),
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }

    timeout_seconds = DEFAULT_TIMEOUT_SECONDS if timeout is None else int(timeout)
    timeout_seconds = max(1, min(timeout_seconds, MAX_TIMEOUT_SECONDS))
    argv = build_gh_argv(args=args, repo=repo)
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env["GITHUB_TOKEN"] = token
    # Prefer token auth over ambient gh keyring login.
    env.pop("GH_ENTERPRISE_TOKEN", None)

    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"gh timed out after {timeout_seconds}s",
            "error_type": "timeout",
            "argv": argv,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }
    except OSError as exc:
        return {
            "ok": False,
            "error": f"failed to start gh: {exc}",
            "error_type": "spawn_error",
            "argv": argv,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }

    stdout, stdout_truncated = _cap_output(_redact_secret(completed.stdout or "", token))
    stderr, stderr_truncated = _cap_output(_redact_secret(completed.stderr or "", token))
    ok = completed.returncode == 0
    payload: dict[str, Any] = {
        "ok": ok,
        "argv": argv,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }
    if stdout_truncated or stderr_truncated:
        payload["truncated"] = True
    if not ok:
        error_text = stderr.strip() or stdout.strip() or f"gh exited with {completed.returncode}"
        payload["error"] = _redact_secret(error_text, token)
        payload["error_type"] = "gh_error"
    return payload


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_TIMEOUT_SECONDS",
    "build_gh_argv",
    "denied_gh_command",
    "positional_gh_tokens",
    "run_gh",
]
