"""Authenticated remote reads and revision checks for isolated repair checkouts."""

from urllib.parse import urlsplit

from integrations.git.errors import BRANCH_FAILED, GitCommandError
from integrations.git.local import _run_git, _token_auth_env


def origin_url(workspace: str) -> str:
    """Return the configured origin URL without applying transport URL rewrites."""
    result = _run_git(workspace, "config", "--get", "remote.origin.url")
    return result.stdout.strip() if result.returncode == 0 else ""


def origin_push_urls(workspace: str) -> list[str]:
    """Return every configured push destination, falling back to the fetch origin."""
    result = _run_git(workspace, "config", "--get-all", "remote.origin.pushurl")
    return result.stdout.splitlines() if result.returncode == 0 else [origin_url(workspace)]


def remote_branch_sha(workspace: str, branch: str, *, token: str | None = None) -> str:
    """Read the current remote branch head before publishing a repair."""
    parsed = urlsplit(origin_url(workspace))
    env = (
        _token_auth_env(token, f"https://{parsed.netloc}/")
        if token and parsed.scheme == "https"
        else None
    )
    result = _run_git(workspace, "ls-remote", "origin", f"refs/heads/{branch}", env=env)
    if result.returncode:
        raise GitCommandError(BRANCH_FAILED, "Could not verify the remote branch head.")
    rows = result.stdout.split()
    return rows[0] if rows else ""


def fetch_local_branch(workspace: str, branch: str, *, token: str | None = None) -> None:
    """Fetch a branch into a local ref using the request's credentials."""
    parsed = urlsplit(origin_url(workspace))
    env = (
        _token_auth_env(token, f"https://{parsed.netloc}/")
        if token and parsed.scheme == "https"
        else None
    )
    result = _run_git(
        workspace, "fetch", "origin", f"refs/heads/{branch}:refs/heads/{branch}", env=env
    )
    if result.returncode:
        raise GitCommandError(BRANCH_FAILED, "Could not fetch the repair branch.")


def ensure_head_revision(workspace: str, expected: str) -> None:
    """Require the checkout to match the revision whose failures were inspected."""
    result = _run_git(workspace, "rev-parse", "HEAD")
    if result.returncode or result.stdout.strip() != expected:
        raise GitCommandError(
            "checks_superseded", "The checkout revision changed; no repair was made."
        )
