"""Detection, version probing, and authentication status inspection for GitHub CLI (`gh`)."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum

from config.constants.github import GITHUB_DEFAULT_HOST, GITHUB_HOST_ENV
from integrations.llm_cli.binary_resolver import (
    candidate_binary_names,
    default_cli_fallback_paths,
    resolve_cli_binary,
)
from integrations.llm_cli.probe_utils import run_version_probe
from integrations.llm_cli.semver_utils import parse_semver_three_part

logger = logging.getLogger(__name__)

DEFAULT_GH_PROBE_TIMEOUT_SECONDS: float = 5.0

# Text output fallback regexes for `gh auth status`
_GH_LOGGED_IN_ACCOUNT_LINE = re.compile(
    r"(?m)^\s*(?:✓\s*)?logged in to\s+(?P<host>\S+)\s+(?:account|as)\s+(?P<login>\S+)",
    re.IGNORECASE,
)
_GH_ACTIVE_ACCOUNT_LINE = re.compile(
    r"(?m)^\s*-\s*active account:\s*true\b",
    re.IGNORECASE,
)
_GH_ACCOUNT_LINE = re.compile(
    r"(?m)^\s*-\s*account:\s*(?P<login>\S+)",
    re.IGNORECASE,
)

_GH_LOGGED_OUT_PHRASES = (
    "not logged in",
    "you are not logged into any github hosts",
    "you are not logged into any hosts",
    "no accounts",
)


class GitHubCLIAuthStatus(StrEnum):
    """Three-state authentication status of the GitHub CLI session."""

    AUTHENTICATED = "authenticated"
    NOT_AUTHENTICATED = "not_authenticated"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GitHubCLIHostAccount:
    """An authenticated host and account detected from the GitHub CLI session."""

    hostname: str
    username: str
    active: bool = False


@dataclass(frozen=True)
class GitHubCLIProbeResult:
    """Outcome of probing the local GitHub CLI binary and its authentication."""

    installed: bool
    binary_path: str | None = None
    version: str | None = None
    auth_status: GitHubCLIAuthStatus = GitHubCLIAuthStatus.UNKNOWN
    active_account: GitHubCLIHostAccount | None = None
    accounts: tuple[GitHubCLIHostAccount, ...] = ()
    detail: str = ""


def _fallback_gh_paths() -> list[str]:
    return default_cli_fallback_paths("gh")


def resolve_gh_binary() -> str | None:
    """Locate the `gh` binary using GH_BIN, PATH, and standard fallback install paths."""
    return resolve_cli_binary(
        explicit_env_key="GH_BIN",
        binary_names=candidate_binary_names("gh"),
        fallback_paths=_fallback_gh_paths,
    )


def _target_gh_hostname(explicit_host: str | None = None) -> str:
    """Return the normalized target GitHub hostname from argument, env, or default."""
    candidate = (
        explicit_host
        or os.environ.get(GITHUB_HOST_ENV, "")
        or os.environ.get("COPILOT_GH_HOST", "")
    ).strip()
    if not candidate:
        return GITHUB_DEFAULT_HOST
    normalized = candidate.lower().rstrip("/").removeprefix("https://").removeprefix("http://")
    return normalized or GITHUB_DEFAULT_HOST


def _parse_gh_auth_json(
    raw_json: str, target_host: str | None = None
) -> tuple[GitHubCLIAuthStatus, GitHubCLIHostAccount | None, tuple[GitHubCLIHostAccount, ...], str]:
    """Parse `gh auth status --json hosts` structured output."""
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return GitHubCLIAuthStatus.UNKNOWN, None, (), "Failed to parse JSON auth status."

    if not isinstance(data, dict):
        return GitHubCLIAuthStatus.UNKNOWN, None, (), "Unexpected JSON response structure."

    hosts_dict = data.get("hosts")
    if not isinstance(hosts_dict, dict) or not hosts_dict:
        return (
            GitHubCLIAuthStatus.NOT_AUTHENTICATED,
            None,
            (),
            "No authenticated hosts reported by GitHub CLI.",
        )

    all_accounts: list[GitHubCLIHostAccount] = []
    active_account: GitHubCLIHostAccount | None = None

    for host_name, entries in hosts_dict.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            login = str(entry.get("login") or "").strip()
            state = str(entry.get("state") or "").strip().lower()
            is_active = bool(entry.get("active"))
            # If state is reported, it must be success or ok
            if state and state not in {"success", "ok"}:
                continue
            if not login:
                continue
            account = GitHubCLIHostAccount(hostname=host_name, username=login, active=is_active)
            all_accounts.append(account)
            if (
                is_active
                and (target_host is None or host_name.lower() == target_host.lower())
                and active_account is None
            ):
                active_account = account

    if not all_accounts:
        return (
            GitHubCLIAuthStatus.NOT_AUTHENTICATED,
            None,
            (),
            "No authenticated accounts found.",
        )

    # If specific host requested, select account for that host
    if target_host:
        target_accounts = [a for a in all_accounts if a.hostname.lower() == target_host.lower()]
        if not target_accounts:
            return (
                GitHubCLIAuthStatus.NOT_AUTHENTICATED,
                None,
                (),
                f"No authenticated account found for host '{target_host}'.",
            )
        chosen = (
            active_account
            if (active_account and active_account.hostname.lower() == target_host.lower())
            else target_accounts[0]
        )
        return (
            GitHubCLIAuthStatus.AUTHENTICATED,
            chosen,
            tuple(all_accounts),
            f"Authenticated as @{chosen.username} on {chosen.hostname}.",
        )

    chosen = active_account or all_accounts[0]
    return (
        GitHubCLIAuthStatus.AUTHENTICATED,
        chosen,
        tuple(all_accounts),
        f"Authenticated as @{chosen.username} on {chosen.hostname}.",
    )


def _parse_gh_auth_text(
    combined_output: str, target_host: str | None = None
) -> tuple[GitHubCLIAuthStatus, GitHubCLIHostAccount | None, tuple[GitHubCLIHostAccount, ...], str]:
    """Parse plain text output from `gh auth status` as a fallback."""
    lowered = combined_output.lower()

    if any(phrase in lowered for phrase in _GH_LOGGED_OUT_PHRASES):
        return (
            GitHubCLIAuthStatus.NOT_AUTHENTICATED,
            None,
            (),
            "GitHub CLI reports not logged in.",
        )

    accounts: list[GitHubCLIHostAccount] = []
    for match in _GH_LOGGED_IN_ACCOUNT_LINE.finditer(combined_output):
        host = match.group("host").strip()
        login = match.group("login").strip()
        if host and login:
            accounts.append(GitHubCLIHostAccount(hostname=host, username=login, active=True))

    if not accounts:
        if "logged in" in lowered or "active account: true" in lowered:
            return (
                GitHubCLIAuthStatus.AUTHENTICATED,
                None,
                (),
                "Authenticated session detected via GitHub CLI.",
            )
        return (
            GitHubCLIAuthStatus.UNKNOWN,
            None,
            (),
            "Ambiguous output from GitHub CLI auth status.",
        )

    if target_host:
        target_matches = [a for a in accounts if a.hostname.lower() == target_host.lower()]
        if not target_matches:
            return (
                GitHubCLIAuthStatus.NOT_AUTHENTICATED,
                None,
                (),
                f"No account logged in for host '{target_host}'.",
            )
        chosen = target_matches[0]
    else:
        chosen = accounts[0]

    return (
        GitHubCLIAuthStatus.AUTHENTICATED,
        chosen,
        tuple(accounts),
        f"Authenticated as @{chosen.username} on {chosen.hostname}.",
    )


def probe_gh_auth(
    binary_path: str,
    *,
    hostname: str | None = None,
    timeout_sec: float = DEFAULT_GH_PROBE_TIMEOUT_SECONDS,
) -> tuple[GitHubCLIAuthStatus, GitHubCLIHostAccount | None, tuple[GitHubCLIHostAccount, ...], str]:
    """Inspect `gh auth status` using JSON output with text fallback."""
    target_host = hostname if hostname else None

    # 1. Attempt JSON probe
    argv = [binary_path, "auth", "status", "--json", "hosts"]
    if target_host and target_host.lower() != GITHUB_DEFAULT_HOST:
        argv.extend(["--hostname", target_host])

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
    except FileNotFoundError:
        return (
            GitHubCLIAuthStatus.UNKNOWN,
            None,
            (),
            f"GitHub CLI binary not found at '{binary_path}'.",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (
            GitHubCLIAuthStatus.UNKNOWN,
            None,
            (),
            f"Failed to run `gh auth status`: {exc}",
        )

    combined_text = f"{proc.stdout or ''}\n{proc.stderr or ''}".strip()

    # JSON output may be in stdout
    if proc.stdout and proc.stdout.strip().startswith("{"):
        auth_status, active, all_acc, detail = _parse_gh_auth_json(proc.stdout, target_host)
        if auth_status != GitHubCLIAuthStatus.UNKNOWN:
            return auth_status, active, all_acc, detail

    # 2. Text fallback probe if JSON was unsupported or returncode != 0
    if proc.returncode != 0 and any(
        phrase in combined_text.lower() for phrase in _GH_LOGGED_OUT_PHRASES
    ):
        return (
            GitHubCLIAuthStatus.NOT_AUTHENTICATED,
            None,
            (),
            "GitHub CLI reports not logged in.",
        )

    # Retry text-only if JSON failed or flag unrecognized
    text_argv = [binary_path, "auth", "status"]
    if target_host and target_host.lower() != GITHUB_DEFAULT_HOST:
        text_argv.extend(["--hostname", target_host])

    try:
        text_proc = subprocess.run(
            text_argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (
            GitHubCLIAuthStatus.UNKNOWN,
            None,
            (),
            f"Failed to run text probe `gh auth status`: {exc}",
        )

    combined_text = f"{text_proc.stdout or ''}\n{text_proc.stderr or ''}".strip()

    # gh auth status exits with 1 if any account on any host has an issue, or if not logged in
    status, active, all_acc, detail = _parse_gh_auth_text(combined_text, target_host)
    if status == GitHubCLIAuthStatus.UNKNOWN and text_proc.returncode != 0:
        # Check if error indicates execution problem vs not logged in
        if any(phrase in combined_text.lower() for phrase in _GH_LOGGED_OUT_PHRASES):
            return (
                GitHubCLIAuthStatus.NOT_AUTHENTICATED,
                None,
                (),
                "GitHub CLI reports not logged in.",
            )
        return (
            GitHubCLIAuthStatus.UNKNOWN,
            None,
            (),
            f"`gh auth status` exited with code {text_proc.returncode}: {combined_text or 'unknown error'}",
        )

    return status, active, all_acc, detail


def detect_github_cli(
    *,
    hostname: str | None = None,
    timeout_sec: float = DEFAULT_GH_PROBE_TIMEOUT_SECONDS,
) -> GitHubCLIProbeResult:
    """Perform complete detection: resolve binary, check version, and probe auth."""
    binary = resolve_gh_binary()
    if not binary:
        return GitHubCLIProbeResult(
            installed=False,
            detail=(
                "GitHub CLI (`gh`) not found on PATH or standard install locations. "
                "Install with: `brew install gh` (macOS), `sudo apt install gh` (Linux), "
                "or `winget install GitHub.cli` (Windows)."
            ),
        )

    # Probe version
    version_output, version_error = run_version_probe(binary, timeout_sec=timeout_sec)
    if version_error:
        return GitHubCLIProbeResult(
            installed=False,
            binary_path=binary,
            detail=version_error,
        )

    version = parse_semver_three_part(version_output or "")

    # Probe auth
    auth_status, active_acc, all_acc, auth_detail = probe_gh_auth(
        binary, hostname=hostname, timeout_sec=timeout_sec
    )

    return GitHubCLIProbeResult(
        installed=True,
        binary_path=binary,
        version=version,
        auth_status=auth_status,
        active_account=active_acc,
        accounts=all_acc,
        detail=auth_detail,
    )


__all__ = [
    "DEFAULT_GH_PROBE_TIMEOUT_SECONDS",
    "GitHubCLIAuthStatus",
    "GitHubCLIHostAccount",
    "GitHubCLIProbeResult",
    "detect_github_cli",
    "probe_gh_auth",
    "resolve_gh_binary",
]
