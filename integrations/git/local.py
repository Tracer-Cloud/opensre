"""Thin, safe local-git client (branch / commit / push / status / hashing).

Every call shells out to the ``git`` binary in the target *workspace* with an
explicit argument list (never ``shell=True``) and a bounded timeout, and raises a
neutral :class:`GitCommandError` on failure. The push path is deliberately narrow:
it refuses to create or push a *protected* branch (``main``/``master``/the repo
default) and never uses ``--force`` — the structural half of a "never push to the
base branch" guarantee.

Vendor-neutral: callers pass a token for HTTPS auth, but nothing here is
GitHub-specific.
"""

from __future__ import annotations

import base64
import os
import subprocess
from collections.abc import Sequence
from urllib.parse import urlsplit

from config.constants.git import OPENSRE_COMMIT_COAUTHOR_TRAILER
from integrations.git.errors import (
    BRANCH_FAILED,
    COMMIT_FAILED,
    GIT_UNAVAILABLE,
    NOT_A_GIT_REPO,
    PROTECTED_BRANCH,
    PUSH_FAILED,
    GitCommandError,
)

_GIT_TIMEOUT_SEC = 60
# Networked lookups get a tighter bound so a slow/unreachable remote can't stall
# the whole flow (they always have a safe local fallback).
_REMOTE_TIMEOUT_SEC = 15
# Branch names we refuse to create or push to, on top of the resolved default.
_PROTECTED_BRANCHES = frozenset({"main", "master", "develop", "trunk"})


def _with_opensre_coauthor(message: str) -> str:
    """Return *message* with OpenSRE's GitHub co-author trailer exactly once."""
    stripped = message.rstrip()
    trailer = OPENSRE_COMMIT_COAUTHOR_TRAILER
    if any(line.strip().lower() == trailer.lower() for line in stripped.splitlines()):
        return stripped
    if not stripped:
        return trailer
    return f"{stripped}\n\n{trailer}"


def _run_git(
    workspace: str,
    *args: str,
    env: dict[str, str] | None = None,
    timeout: float = _GIT_TIMEOUT_SEC,
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in *workspace*; raise GitCommandError if git is missing."""
    try:
        return subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as exc:
        raise GitCommandError(GIT_UNAVAILABLE, "git is not installed or not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitCommandError(
            GIT_UNAVAILABLE, f"git command timed out after {timeout:.0f}s."
        ) from exc


def _remote_https_base(workspace: str, remote: str = "origin") -> str:
    """``https://host/`` of *remote* when it uses HTTPS, else "" (http/SSH/file/etc.).

    Only HTTPS qualifies: injecting the token for a plaintext ``http://`` remote
    would send the credential in cleartext on the wire.
    """
    result = _run_git(workspace, "remote", "get-url", remote)
    if result.returncode != 0:
        return ""
    parsed = urlsplit(result.stdout.strip())
    if parsed.scheme == "https" and parsed.hostname:
        return f"https://{parsed.hostname}/"
    return ""


def _token_auth_env(token: str, base_url: str) -> dict[str, str]:
    """Env that injects an HTTP Authorization header scoped to *base_url* for this call.

    Uses git's ``GIT_CONFIG_*`` env-config so the token never appears in argv, the
    remote URL, .git/config, or git's output. The header is scoped via
    ``http.<base_url>.extraheader`` so the token is only sent to that host and never
    forwarded to other HTTPS remotes or redirects. This makes the request use the
    *provided* token instead of whatever stale credential the local git credential
    helper might have cached (the usual cause of a 403 on push).
    """
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = dict(os.environ)
    # Append at the next free index rather than clobbering an existing
    # GIT_CONFIG_COUNT / GIT_CONFIG_KEY_* the caller may already rely on.
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0") or "0")
    except ValueError:
        count = 0
    env[f"GIT_CONFIG_KEY_{count}"] = f"http.{base_url}.extraheader"
    env[f"GIT_CONFIG_VALUE_{count}"] = f"Authorization: Basic {basic}"
    env["GIT_CONFIG_COUNT"] = str(count + 1)
    return env


def is_git_repo(workspace: str) -> bool:
    """True when *workspace* is inside a git work tree."""
    result = _run_git(workspace, "rev-parse", "--is-inside-work-tree")
    return result.returncode == 0 and result.stdout.strip() == "true"


def clone_repository(url: str, workspace: str, *, token: str) -> None:
    """Clone an HTTPS repository with credentials confined to the child's environment."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise GitCommandError(NOT_A_GIT_REPO, "Cloning requires an HTTPS repository URL.")
    env = _token_auth_env(token, f"https://{parsed.netloc}/")
    env["GIT_TERMINAL_PROMPT"] = "0"
    result = _run_git(
        os.path.dirname(workspace),
        "clone",
        "--",
        url,
        workspace,
        env=env,
    )
    if result.returncode != 0:
        raise GitCommandError(NOT_A_GIT_REPO, "Could not clone the selected repository.")


def ensure_git_repo(workspace: str) -> None:
    if not is_git_repo(workspace):
        raise GitCommandError(NOT_A_GIT_REPO, f"{workspace} is not a git repository.")


def current_branch(workspace: str) -> str:
    """Name of the currently checked-out branch (empty on detached HEAD)."""
    result = _run_git(workspace, "rev-parse", "--abbrev-ref", "HEAD")
    branch = result.stdout.strip()
    return "" if branch in ("", "HEAD") else branch


def _remote_default_branch(workspace: str, token: str | None) -> str:
    """The remote's default branch via ``ls-remote --symref`` (authoritative).

    Bounded by a short timeout and returns "" on any failure/timeout, so a slow or
    unreachable remote never stalls or aborts the caller — they fall back locally.
    """
    base = _remote_https_base(workspace, "origin")
    env = _token_auth_env(token, base) if (token and base) else None
    try:
        result = _run_git(
            workspace,
            "ls-remote",
            "--symref",
            "origin",
            "HEAD",
            env=env,
            timeout=_REMOTE_TIMEOUT_SEC,
        )
    except GitCommandError:
        return ""
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        # "ref: refs/heads/main\tHEAD"
        if line.startswith("ref:"):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1].removeprefix("refs/heads/")
    return ""


def default_branch(workspace: str, *, token: str | None = None) -> str:
    """Resolve the repo's default branch (the usual PR base), or "" if unknown.

    Prefers the local ``origin/HEAD`` pointer; if it isn't configured (common on
    fresh clones), asks the remote directly. Returns "" when neither is available
    (e.g. offline) rather than guessing the current branch — callers must decide
    what to do so a PR never silently targets the wrong base.
    """
    result = _run_git(workspace, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().removeprefix("origin/")
    return _remote_default_branch(workspace, token)


def short_head(workspace: str) -> str:
    """Short SHA of HEAD, or "" if it can't be resolved (e.g. an unborn HEAD)."""
    result = _run_git(workspace, "rev-parse", "--short", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else ""


def changed_paths(workspace: str) -> list[str]:
    """Paths with staged/unstaged/untracked changes (individual files, not dirs).

    Uses ``-z`` (NUL-separated) so paths are returned verbatim: git's default
    porcelain C-quotes filenames with spaces, quotes, or non-ASCII bytes, which
    would then not match on ``git add``/``hash-object``.
    """
    result = _run_git(workspace, "status", "--porcelain", "-z", "--untracked-files=all")
    tokens = result.stdout.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(tokens):
        record = tokens[i]
        i += 1
        if len(record) < 3:
            continue
        # Porcelain: "XY <path>". Rename/copy (R/C) records carry the original path
        # in the next NUL-terminated token.
        path = record[3:]
        if path:
            paths.append(path)
        if record[0] in ("R", "C"):
            orig = tokens[i] if i < len(tokens) else ""
            i += 1
            # A rename deletes the original, so it must be committed too; a copy
            # leaves the original untouched, so it is excluded.
            if record[0] == "R" and orig:
                paths.append(orig)
    return paths


def committed_paths_since(workspace: str, revision: str) -> list[str]:
    """Return committed paths changed since a trusted revision, including rename sources."""
    result = _run_git(
        workspace, "diff", "--name-only", "--no-renames", "-z", revision, "HEAD", "--"
    )
    if result.returncode != 0:
        raise GitCommandError(NOT_A_GIT_REPO, "Could not verify the repair's committed changes.")
    return [path for path in result.stdout.split("\0") if path]


def file_fingerprints(workspace: str, paths: Sequence[str]) -> dict[str, str]:
    """Map each path to a git hash of its current worktree content ("" if unreadable).

    Lets a caller tell whether a file that was already dirty before a run was
    actually *changed* (hash differs) versus left untouched (same hash).
    """
    fingerprints: dict[str, str] = dict.fromkeys(paths, "")
    # Hash all files in a single git invocation (one process, not one per file).
    # Deleted/unreadable paths are filtered out first so they don't fail the batch;
    # they keep the "" fingerprint.
    existing = [p for p in paths if os.path.isfile(os.path.join(workspace, p))]
    if not existing:
        return fingerprints
    result = _run_git(workspace, "hash-object", "--", *existing)
    hashes = result.stdout.splitlines()
    if result.returncode == 0 and len(hashes) == len(existing):
        for path, digest in zip(existing, hashes):
            fingerprints[path] = digest.strip()
    return fingerprints


def assert_not_protected(branch: str, *, protected_extra: str = "") -> None:
    """Raise unless *branch* is a safe, non-base feature branch to push to."""
    name = branch.strip()
    protected = set(_PROTECTED_BRANCHES)
    if protected_extra.strip():
        protected.add(protected_extra.strip())
    if not name or name in protected:
        raise GitCommandError(
            PROTECTED_BRANCH,
            f"Refusing to create or push protected branch '{name or '(empty)'}'. "
            "Work is always shipped on a fresh namespaced branch, never the base branch.",
        )


def create_branch(workspace: str, branch: str, *, base_default: str = "") -> None:
    """Create and switch to *branch* off the current HEAD (protected-name guarded)."""
    assert_not_protected(branch, protected_extra=base_default)
    result = _run_git(workspace, "checkout", "-b", branch)
    if result.returncode != 0:
        raise GitCommandError(
            BRANCH_FAILED, f"Could not create branch '{branch}': {result.stderr.strip()}"
        )


def checkout_branch(workspace: str, branch: str) -> None:
    """Switch to an already-existing local *branch* (does not create one).

    Used to put the workspace on a known branch (typically the resolved base)
    before creating a new branch off it, so the new branch's parent is never
    whatever unrelated branch the workspace happened to have checked out.
    """
    result = _run_git(workspace, "checkout", branch)
    if result.returncode != 0:
        raise GitCommandError(
            BRANCH_FAILED, f"Could not check out branch '{branch}': {result.stderr.strip()}"
        )


def commit_paths(workspace: str, paths: Sequence[str], message: str) -> None:
    """Stage and commit *only* the given paths, excluding any other WIP in the tree.

    ``git add`` registers the paths (so newly created files are tracked), and
    ``git commit --only`` commits exactly those paths — disregarding any other
    staged or unstaged changes the developer may have in the working tree.
    """
    if not paths:
        raise GitCommandError(COMMIT_FAILED, "no files to commit.")

    # Register the paths that still exist (new/modified files) so ``--only`` can
    # commit them; deleted paths (e.g. a rename's original) are skipped here and
    # handled by ``git commit --only``, which records their removal.
    existing = [p for p in paths if os.path.isfile(os.path.join(workspace, p))]
    if existing:
        add = _run_git(workspace, "add", "--", *existing)
        if add.returncode != 0:
            raise GitCommandError(COMMIT_FAILED, f"git add failed: {add.stderr.strip()}")

    commit = _run_git(
        workspace,
        "commit",
        "--only",
        "-m",
        _with_opensre_coauthor(message),
        "--",
        *paths,
    )
    if commit.returncode != 0:
        raise GitCommandError(COMMIT_FAILED, f"git commit failed: {commit.stderr.strip()}")


def push_branch(
    workspace: str,
    branch: str,
    *,
    remote: str = "origin",
    base_default: str = "",
    token: str | None = None,
    allow_protected: bool = False,
) -> None:
    """Push *branch* to *remote* with upstream tracking. Never force, never base branch.

    When *token* is given and *remote* is an HTTPS URL, the push authenticates with
    that token (via an ephemeral, host-scoped HTTP header) instead of the machine's
    cached git credentials. For SSH/other remotes the token is not injected (the
    transport authenticates itself).

    ``allow_protected`` skips the protected-branch guard. Reserve it for
    approval-gated flows where the user explicitly requested a direct push to
    that branch (e.g. a branch-targeted CI fix on ``main``); never pass it for
    ordinary feature-branch shipping.
    """
    if not allow_protected:
        assert_not_protected(branch, protected_extra=base_default)
    env = None
    if token:
        base = _remote_https_base(workspace, remote)
        if base:
            env = _token_auth_env(token, base)
    result = _run_git(workspace, "push", "--set-upstream", remote, branch, env=env)
    if result.returncode != 0:
        raise GitCommandError(
            PUSH_FAILED, f"git push to {remote}/{branch} failed: {result.stderr.strip()}"
        )
