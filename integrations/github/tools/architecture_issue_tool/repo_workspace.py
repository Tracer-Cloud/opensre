"""Clone GitHub repositories into the architecture audit workspace."""

from __future__ import annotations

import base64
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from config.constants.paths import OPENSRE_TMP_DIR, ensure_opensre_tmp_dir
from infrastructure.process.turn_capacity import HEAVY_WORK_BUSY_MESSAGE, heavy_work_slot

_GITHUB_HTTPS_BASE = "https://github.com/"
_GIT_CLONE_TIMEOUT_SEC = 120.0
_GIT_REMOTE_TIMEOUT_SEC = 15.0
_ARCHITECTURE_WORKSPACE_DIR = OPENSRE_TMP_DIR / "workspace"
_AUDIT_DIR_PREFIX = "audit-"
# Far longer than any audit turn runs, so only abandoned clones are this old.
_STALE_AUDIT_MAX_AGE_SEC = 24 * 60 * 60.0

logger = logging.getLogger(__name__)

_SHA_REF_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


class WorkspaceError(Exception):
    """Failed to prepare a repository workspace for scanning."""


@dataclass(frozen=True)
class RepoWorkspace:
    """Resolved local workspace for a GitHub repository audit."""

    owner: str
    repo: str
    ref: str
    root: Path


def github_remote_url(owner: str, repo: str) -> str:
    """Return the HTTPS git remote URL for a GitHub repository."""
    return f"{_GITHUB_HTTPS_BASE}{owner.strip()}/{repo.strip()}.git"


def architecture_workspace_dir() -> Path:
    """Return the shared root that holds one private directory per architecture audit."""
    ensure_opensre_tmp_dir()
    return _ARCHITECTURE_WORKSPACE_DIR


def _remove_tree(path: Path, *, action: str) -> None:
    """Delete *path* recursively; raise WorkspaceError on any failure.

    Unlike ``shutil.rmtree(..., ignore_errors=True)``, this never reports success
    when the tree is only partially removed (or not removed at all).
    """
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise WorkspaceError(f"{action} failed: could not remove {path}: {exc}") from exc
    if path.exists():
        raise WorkspaceError(f"{action} failed: path still exists after removal ({path})")


#: Audit directories this process created and has not cleaned up, by the session
#: that owns each ("" for a caller without one). Only that owner may delete one.
_live_audits: dict[Path, str] = {}
_live_audits_lock = threading.Lock()


def _sweep_stale_entries(root: Path) -> None:
    """Best-effort removal of root entries untouched for longer than any audit runs.

    A live audit of this process is never swept, whatever its age; anything else
    older than the cutoff is a leftover of a run that died.
    """
    cutoff = time.time() - _STALE_AUDIT_MAX_AGE_SEC
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    with _live_audits_lock:
        live = set(_live_audits)
    for entry in entries:
        try:
            if entry.resolve() in live or entry.lstat().st_mtime > cutoff:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("Could not remove stale architecture workspace entry %s: %s", entry, exc)


def prepare_architecture_workspace(*, audit_owner: str = "") -> Path:
    """Create and return a fresh directory for one audit under the shared root.

    Each call gets its own directory, so concurrent audits never share or delete
    each other's clone. ``audit_owner`` (the calling session) is the only caller
    :func:`cleanup_architecture_workspace` lets delete it. Leftovers older than
    any audit can run are swept first.
    """
    root = architecture_workspace_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
        _sweep_stale_entries(root)
        directory = Path(tempfile.mkdtemp(prefix=_AUDIT_DIR_PREFIX, dir=root)).resolve()
    except OSError as exc:
        raise WorkspaceError(f"prepare architecture workspace failed: {exc}") from exc
    with _live_audits_lock:
        _live_audits[directory] = audit_owner
    return directory


def cleanup_architecture_workspace(path: str | Path, *, audit_owner: str = "") -> Path:
    """Delete one audit's directory, only for the session that created it.

    Refuses the shared root, anything outside it, and an audit directory another
    session owns or this process did not create (leftovers are swept by age).
    """
    root = architecture_workspace_dir().resolve()
    target = Path(path).expanduser().resolve()
    if target.parent != root:
        raise WorkspaceError(
            f"cleanup refused: path is not an audit directory inside the "
            f"architecture workspace ({root})"
        )
    with _live_audits_lock:
        owner = _live_audits.get(target)
    if owner is None or owner != audit_owner:
        raise WorkspaceError("cleanup refused: that directory is not an audit this session started")
    _remove_tree(target, action="cleanup architecture workspace")
    with _live_audits_lock:
        _live_audits.pop(target, None)
    return target


def _run_git(
    cwd: Path,
    *args: str,
    env: dict[str, str] | None = None,
    timeout: float = _GIT_CLONE_TIMEOUT_SEC,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as exc:
        raise WorkspaceError("git is not installed or not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError(f"git command timed out after {timeout:.0f}s.") from exc


def _token_auth_env(token: str, base_url: str) -> dict[str, str]:
    """Inject an HTTPS Authorization header scoped to *base_url* via git config env.

    Kept local so this tools package does not import ``integrations`` (layer peers).
    """
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = dict(os.environ)
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0") or "0")
    except ValueError:
        count = 0
    env[f"GIT_CONFIG_KEY_{count}"] = f"http.{base_url}.extraheader"
    env[f"GIT_CONFIG_VALUE_{count}"] = f"Authorization: Basic {basic}"
    env["GIT_CONFIG_COUNT"] = str(count + 1)
    return env


def _auth_env(token: str | None) -> dict[str, str] | None:
    if not token:
        return None
    return _token_auth_env(token, _GITHUB_HTTPS_BASE)


def _remote_default_branch(remote_url: str, *, token: str | None) -> str:
    env = _auth_env(token)
    with tempfile.TemporaryDirectory(prefix="opensre-arch-remote-") as tmp:
        result = _run_git(
            Path(tmp),
            "ls-remote",
            "--symref",
            remote_url,
            "HEAD",
            env=env,
            timeout=_GIT_REMOTE_TIMEOUT_SEC,
        )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "ls-remote failed"
        raise WorkspaceError(f"Could not resolve default branch: {detail}")

    for line in result.stdout.splitlines():
        if line.startswith("ref:"):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1].removeprefix("refs/heads/")
    raise WorkspaceError("Could not resolve default branch from ls-remote output.")


def _looks_like_sha(ref: str) -> bool:
    return bool(_SHA_REF_RE.fullmatch(ref.strip()))


def _git_ok(
    result: subprocess.CompletedProcess[str],
    *,
    fallback: str,
) -> None:
    if result.returncode == 0:
        return
    detail = result.stderr.strip() or result.stdout.strip() or fallback
    raise WorkspaceError(detail)


def _shallow_clone_sha(
    *,
    remote_url: str,
    destination: Path,
    sha: str,
    token: str | None,
) -> None:
    """Fetch a single commit SHA without requiring it to be the remote HEAD.

    ``git clone --depth 1`` only materializes the tip of the default branch, so
    ``checkout <sha>`` fails for any non-HEAD commit. Init + ``fetch --depth 1
    origin <sha>`` asks the remote for that object directly.
    """
    env = _auth_env(token)
    destination.mkdir(parents=True, exist_ok=True)
    _git_ok(_run_git(destination, "init", env=env), fallback="git init failed")
    _git_ok(
        _run_git(destination, "remote", "add", "origin", remote_url, env=env),
        fallback="git remote add failed",
    )
    _git_ok(
        _run_git(destination, "fetch", "--depth", "1", "origin", sha, env=env),
        fallback=f"git fetch failed for SHA {sha}",
    )
    _git_ok(
        _run_git(destination, "checkout", "--detach", "FETCH_HEAD", env=env),
        fallback=f"git checkout failed for SHA {sha}",
    )


def _shallow_clone(
    *,
    remote_url: str,
    destination: Path,
    ref: str,
    token: str | None,
) -> None:
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    env = _auth_env(token)

    if _looks_like_sha(ref):
        _shallow_clone_sha(
            remote_url=remote_url,
            destination=destination,
            sha=ref.strip(),
            token=token,
        )
        return

    result = _run_git(
        parent,
        "clone",
        "--depth",
        "1",
        "--branch",
        ref,
        remote_url,
        str(destination),
        env=env,
    )
    _git_ok(result, fallback="git clone failed")


def clone_github_repo(
    owner: str,
    repo: str,
    *,
    ref: str = "",
    token: str | None = None,
    local_path: str | None = None,
    stop: Callable[[], bool] | None = None,
    audit_owner: str = "",
) -> RepoWorkspace:
    """Clone *owner*/*repo* into a fresh audit directory (or use *local_path*).

    Unlike :func:`cloned_github_repo`, this does **not** delete the clone on
    return — callers must pass the returned ``root`` to
    :func:`cleanup_architecture_workspace`. The clone waits for a process-wide
    heavy-work slot; ``stop`` (the turn's cancel flag) ends that wait early, and
    a refusal raises :class:`WorkspaceError`. ``audit_owner`` (the calling
    session) is the only caller that may clean the clone up.
    """
    normalized_owner = owner.strip()
    normalized_repo = repo.strip()
    if not normalized_owner or not normalized_repo:
        raise WorkspaceError("owner and repo are required.")

    if local_path:
        root = Path(local_path).expanduser().resolve()
        if not root.is_dir():
            raise WorkspaceError(f"local_path is not a directory: {root}")
        return RepoWorkspace(
            owner=normalized_owner,
            repo=normalized_repo,
            ref=ref.strip(),
            root=root,
        )

    remote_url = github_remote_url(normalized_owner, normalized_repo)
    effective_ref = ref.strip() or _remote_default_branch(remote_url, token=token)
    destination = prepare_architecture_workspace(audit_owner=audit_owner)

    try:
        with heavy_work_slot(stop=stop) as started:
            if started:
                _shallow_clone(
                    remote_url=remote_url,
                    destination=destination,
                    ref=effective_ref,
                    token=token,
                )
        if not started:
            raise WorkspaceError(HEAVY_WORK_BUSY_MESSAGE)
    except Exception:
        cleanup_architecture_workspace(destination, audit_owner=audit_owner)
        raise

    return RepoWorkspace(
        owner=normalized_owner,
        repo=normalized_repo,
        ref=effective_ref,
        root=destination,
    )


@contextmanager
def cloned_github_repo(
    owner: str,
    repo: str,
    *,
    ref: str = "",
    token: str | None = None,
    local_path: str | None = None,
) -> Iterator[RepoWorkspace]:
    """Yield a workspace, deleting this audit's clone directory on exit.

    When *local_path* is provided (tests/dev only), the path is yielded as-is and
    never deleted. Otherwise a shallow clone is created in a fresh directory
    under ``OPENSRE_TMP_DIR/workspace`` and removed on exit.
    """
    workspace = clone_github_repo(
        owner,
        repo,
        ref=ref,
        token=token,
        local_path=local_path,
    )
    try:
        yield workspace
    finally:
        if local_path is None:
            cleanup_architecture_workspace(workspace.root)
