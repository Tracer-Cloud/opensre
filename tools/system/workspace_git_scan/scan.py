"""Discover local git repositories and their recent activity."""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from config.constants.git import GIT_OPTIONAL_LOCKS_ENV, GIT_TERMINAL_PROMPT_ENV

_SKIP_DIR_NAMES = frozenset(
    {
        ".Trash",
        ".cache",
        ".cargo",
        ".git",
        ".npm",
        ".terraform",
        ".venv",
        "Library",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "venv",
    }
)
_WORKFLOW_GLOBS = ("*.yml", "*.yaml")
_GIT_TIMEOUT_SECONDS = 5
_SCAN_BUDGET_SECONDS = 20.0
_CANCEL_POLL_SECONDS = 0.05
_PROGRESS_INTERVAL_SECONDS = 3.0
_SEARCH_PROGRESS = "Looking for git repositories: {} folders checked, {} found"
_MEASURE_PROGRESS = "Counting recent commits: {} of {} repositories done"
_GITHUB_REMOTE_RE = re.compile(
    r"(?:github\.com[:/])(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$", re.IGNORECASE
)


class ScanStop(StrEnum):
    """Why a scan ended before measuring everything it could reach."""

    CANCELLED = "cancelled"
    TIME_BUDGET = "time_budget"


@dataclass(frozen=True)
class RepoActivity:
    """One local repository with its recent commit and CI facts."""

    name: str
    path: str
    origin: str
    github_owner: str
    github_repo: str
    commits: int
    own_commits: int
    """Commits in the window authored with the user's configured git email."""

    uncommitted: int
    has_workflows: bool

    @property
    def github_full_name(self) -> str:
        if self.github_owner and self.github_repo:
            return f"{self.github_owner}/{self.github_repo}"
        return ""


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Repositories found under one root, most active first."""

    root: str
    days: int
    repos: tuple[RepoActivity, ...] = field(default_factory=tuple)
    truncated: bool = False
    stop_reason: ScanStop | None = None
    """Set when the scan stopped early; ``repos`` then holds what was measured before."""

    skipped: tuple[str, ...] = ()
    """Skip paths the walk reached and did not enter."""

    @property
    def total_commits(self) -> int:
        return sum(repo.commits for repo in self.repos)

    @property
    def total_uncommitted(self) -> int:
        return sum(repo.uncommitted for repo in self.repos)

    @property
    def total_own_commits(self) -> int:
        return sum(repo.own_commits for repo in self.repos)


class _Watch:
    """Early-stop checks and throttled progress lines for one scan."""

    def __init__(
        self,
        *,
        should_stop: Callable[[], bool] | None,
        on_progress: Callable[[str], None] | None,
        budget_seconds: float,
        clock: Callable[[], float],
    ) -> None:
        self._should_stop = should_stop
        self._on_progress = on_progress
        self._clock = clock
        started = clock()
        self._deadline = started + budget_seconds
        self._next_poll = started
        self._next_progress = started + _PROGRESS_INTERVAL_SECONDS
        self.stop_reason: ScanStop | None = None

    def stopped(self, *, throttle: bool = False) -> bool:
        """True once the caller cancelled or the budget ran out; stays true after that.

        With *throttle*, *should_stop* is polled at most every ``_CANCEL_POLL_SECONDS``:
        a host's cancel probe can read a file, and the walk asks before every folder.
        """
        if self.stop_reason is not None:
            return True
        now = self._clock()
        if self._should_stop is not None and (not throttle or now >= self._next_poll):
            self._next_poll = now + _CANCEL_POLL_SECONDS
            if self._should_stop():
                self.stop_reason = ScanStop.CANCELLED
                return True
        if now >= self._deadline:
            self.stop_reason = ScanStop.TIME_BUDGET
        return self.stop_reason is not None

    def progress(self, template: str, *counts: int) -> None:
        """Report *template* filled with *counts*, at most once per progress interval."""
        if self._on_progress is None:
            return
        now = self._clock()
        if now < self._next_progress:
            return
        self._next_progress = now + _PROGRESS_INTERVAL_SECONDS
        self._on_progress(template.format(*counts))


def scan_workspace(
    root: Path,
    *,
    days: int = 30,
    max_depth: int = 4,
    max_repos: int = 200,
    skip_paths: Collection[Path] = (),
    should_stop: Callable[[], bool] | None = None,
    on_progress: Callable[[str], None] | None = None,
    budget_seconds: float = _SCAN_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> WorkspaceSnapshot:
    """Walk *root* for git checkouts and measure each one, most active first.

    *root* is always entered; folders in *skip_paths* never are. The scan stops
    early when *should_stop* returns true or the time budget runs out, checked
    before every repository and before every folder of the walk (*should_stop*
    at most every 50 ms there), so one listing or one repository's git calls can
    overrun a stop. The most recently used checkouts are measured first, so an
    early stop or the repository cap keeps the relevant ones.
    """
    watch = _Watch(
        should_stop=should_stop,
        on_progress=on_progress,
        budget_seconds=budget_seconds,
        clock=clock,
    )
    found, skipped = _git_dirs(
        root, max_depth=max_depth, skip_paths=frozenset(skip_paths), watch=watch
    )
    queue = [] if watch.stopped() else sorted(found, key=_last_used, reverse=True)[:max_repos]
    author = _git(root, "config", "--get", "user.email") if queue else ""
    repos: list[RepoActivity] = []
    for repo_dir in queue:
        if watch.stopped():
            break
        watch.progress(_MEASURE_PROGRESS, len(repos), len(queue))
        repos.append(measure_repo(repo_dir, days=days, author=author))
    merged = _fold_clones(repos)
    merged.sort(key=lambda repo: (-repo.commits, repo.name.lower()))
    return WorkspaceSnapshot(
        root=str(root),
        days=days,
        repos=tuple(merged),
        truncated=len(found) > max_repos,
        stop_reason=watch.stop_reason,
        skipped=tuple(str(path) for path in skipped),
    )


def _fold_clones(repos: list[RepoActivity]) -> list[RepoActivity]:
    """Merge checkouts of the same GitHub repository into one row.

    Commits are shared history, so the largest count stands; uncommitted files
    are per checkout and add up. Repositories without a GitHub origin stay as is.
    """
    by_remote: dict[str, RepoActivity] = {}
    folded: list[RepoActivity] = []
    for repo in repos:
        key = repo.github_full_name.lower()
        if not key:
            folded.append(repo)
            continue
        seen = by_remote.get(key)
        if seen is None:
            by_remote[key] = repo
            continue
        by_remote[key] = RepoActivity(
            name=seen.name if seen.commits >= repo.commits else repo.name,
            path=seen.path if seen.commits >= repo.commits else repo.path,
            origin=seen.origin,
            github_owner=seen.github_owner,
            github_repo=seen.github_repo,
            commits=max(seen.commits, repo.commits),
            own_commits=max(seen.own_commits, repo.own_commits),
            uncommitted=seen.uncommitted + repo.uncommitted,
            has_workflows=seen.has_workflows or repo.has_workflows,
        )
    return [*folded, *by_remote.values()]


def measure_repo(repo_dir: Path, *, days: int, author: str = "") -> RepoActivity:
    origin = _git(repo_dir, "remote", "get-url", "origin")
    owner, name = parse_github_remote(origin)
    since = f"--since={days}.days"
    commits = _git(repo_dir, "rev-list", "--count", "--all", since)
    own = (
        _git(repo_dir, "rev-list", "--count", "--all", since, f"--author={author}")
        if author
        else ""
    )
    status = _git(repo_dir, "status", "--porcelain", "--untracked-files=normal")
    return RepoActivity(
        name=repo_dir.name,
        path=str(repo_dir),
        origin=origin,
        github_owner=owner,
        github_repo=name,
        commits=int(commits) if commits.isdigit() else 0,
        own_commits=int(own) if own.isdigit() else 0,
        uncommitted=sum(1 for line in status.splitlines() if line.strip()),
        has_workflows=_has_workflows(repo_dir),
    )


def parse_github_remote(url: str) -> tuple[str, str]:
    """``owner, repo`` from an HTTPS or SSH GitHub remote, else two empty strings."""
    match = _GITHUB_REMOTE_RE.search(url.strip())
    if match is None:
        return "", ""
    return match.group("owner"), match.group("repo")


def _git_dirs(
    root: Path, *, max_depth: int, skip_paths: frozenset[Path], watch: _Watch
) -> tuple[list[Path], list[Path]]:
    """Checkouts up to *max_depth* below *root*, and the skip paths met on the way.

    Never descends into a checkout or a skip path; stops when *watch* does.
    """
    found: list[Path] = []
    skipped: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    checked = 0
    while stack and not watch.stopped(throttle=True):
        watch.progress(_SEARCH_PROGRESS, checked, len(found))
        current, depth = stack.pop()
        checked += 1
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        names = {entry.name for entry in entries}
        if ".git" in names:
            found.append(current)
            continue
        if depth >= max_depth:
            continue
        for entry in entries:
            if entry.name in _SKIP_DIR_NAMES or entry.name.startswith("."):
                continue
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            path = Path(entry.path)
            if path in skip_paths:
                skipped.append(path)
            else:
                stack.append((path, depth + 1))
    return sorted(found), sorted(skipped)


def _last_used(repo_dir: Path) -> float:
    """When the checkout was last worked in: the mtime of ``.git/index``, else of ``.git``.

    git rewrites the index on every add, commit, checkout and merge, so it tracks
    recent work, unlike ``HEAD``, which changes only on a branch switch. A worktree
    or submodule keeps its index elsewhere; its ``.git`` file stands in.
    """
    git = repo_dir / ".git"
    for candidate in (git / "index", git):
        try:
            return candidate.stat().st_mtime
        except OSError:
            continue
    return 0.0


def _has_workflows(repo_dir: Path) -> bool:
    workflows = repo_dir / ".github" / "workflows"
    return any(path.is_file() for pattern in _WORKFLOW_GLOBS for path in workflows.glob(pattern))


def _git(repo_dir: Path, *args: str) -> str:
    """Output of one read-only git call, or ``""`` when it fails or times out.

    git never reads the terminal, never prompts for credentials and never takes
    the optional index lock, so ``git status`` leaves ``.git/index`` untouched.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, GIT_TERMINAL_PROMPT_ENV: "0", GIT_OPTIONAL_LOCKS_ENV: "0"},
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


__all__ = [
    "RepoActivity",
    "ScanStop",
    "WorkspaceSnapshot",
    "measure_repo",
    "parse_github_remote",
    "scan_workspace",
]
