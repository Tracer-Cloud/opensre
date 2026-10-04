"""Discover local git repositories and their recent activity."""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections import deque
from collections.abc import Callable, Collection
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from pathlib import Path

from config.constants.git import GIT_OPTIONAL_LOCKS_ENV, GIT_TERMINAL_PROMPT_ENV
from tools.system.workspace_git_scan.git_files import common_git_dir, configured_origin

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
# git calls run at once while measuring; more only queue for the same disk and cores.
_MEASURE_WORKERS = 8
# The user's own config the scan reads: the email that marks own commits, URL
# rewrite rules, and conditional includes that can hold rules for some repositories only.
_USER_CONFIG_KEYS = r"^(user\.email|url\..+\.insteadof|includeif\..+)$"
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


@dataclass(frozen=True)
class _History:
    """What every worktree of one repository shares: its origin and recent commits."""

    origin: str
    commits: int
    own_commits: int


@dataclass(frozen=True)
class _Worktree:
    """What one checkout holds on its own."""

    uncommitted: int
    has_workflows: bool


@dataclass(frozen=True)
class _UserConfig:
    """The user's git settings the scan needs, as seen from the scan root."""

    email: str
    rewrites: tuple[str, ...] | None
    """Prefixes ``url.<base>.insteadOf`` rules rewrite; None when they are unknowable."""


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
    before every folder of the walk (*should_stop* at most every 50 ms there) and,
    while measuring, before git calls are started and at least every 50 ms. One
    listing can overrun a stop; git calls still running then are not awaited and
    end within their timeout. Checkouts are measured concurrently, the most
    recently used first, so an early stop or the repository cap keeps the relevant
    ones.
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
    repos = _measure(queue, days=days, user=_user_config(root), watch=watch) if queue else []
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


def _measure(
    checkouts: list[Path], *, days: int, user: _UserConfig, watch: _Watch
) -> list[RepoActivity]:
    """Measure *checkouts* on a thread pool, started in their order, until done or stopped.

    Each repository's history is read once, from its first checkout, for all its
    worktrees. Returns the checkouts measured in full, in their order.
    """
    git_dirs = {checkout: common_git_dir(checkout) for checkout in checkouts}
    worktrees_of: dict[Path, list[Path]] = {}
    jobs: deque[tuple[Path, Callable[[], _History | _Worktree]]] = deque()
    for checkout, git_dir in git_dirs.items():
        if git_dir not in worktrees_of:
            worktrees_of[git_dir] = []
            history = partial(
                _history,
                checkout,
                git_dir=git_dir,
                days=days,
                author=user.email,
                rewrites=user.rewrites,
            )
            jobs.append((git_dir, history))
        worktrees_of[git_dir].append(checkout)
        jobs.append((checkout, partial(_worktree, checkout)))
    histories: dict[Path, _History] = {}
    worktrees: dict[Path, _Worktree] = {}
    measured = 0
    in_flight: dict[Future[_History | _Worktree], Path] = {}
    pool = ThreadPoolExecutor(max_workers=_MEASURE_WORKERS, thread_name_prefix="workspace-scan")
    try:
        while True:
            for future in [future for future in in_flight if future.done()]:
                key = in_flight.pop(future)
                result = future.result()
                if isinstance(result, _History):
                    histories[key] = result
                    measured += sum(1 for checkout in worktrees_of[key] if checkout in worktrees)
                else:
                    worktrees[key] = result
                    if git_dirs[key] in histories:
                        measured += 1
            if not (jobs or in_flight) or watch.stopped():
                break
            watch.progress(_MEASURE_PROGRESS, measured, len(checkouts))
            while jobs and len(in_flight) < _MEASURE_WORKERS:
                key, job = jobs.popleft()
                in_flight[pool.submit(job)] = key
            wait(in_flight, timeout=_CANCEL_POLL_SECONDS, return_when=FIRST_COMPLETED)
    finally:
        # A stopped scan does not wait: running git calls end within their timeout.
        pool.shutdown(wait=False, cancel_futures=True)
    return [
        _activity(checkout, histories[git_dir], worktrees[checkout])
        for checkout, git_dir in git_dirs.items()
        if checkout in worktrees and git_dir in histories
    ]


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
    """Measure one checkout on its own, asking git for its origin."""
    git_dir = common_git_dir(repo_dir)
    history = _history(repo_dir, git_dir=git_dir, days=days, author=author, rewrites=None)
    return _activity(repo_dir, history, _worktree(repo_dir))


def _history(
    checkout: Path, *, git_dir: Path, days: int, author: str, rewrites: Collection[str] | None
) -> _History:
    """Origin and recent commits of *checkout*'s repository, the same from every worktree.

    One ``git log --all`` prints an author email per commit in the window, the
    commits ``rev-list --count --all --since`` counts; own commits are those whose
    email equals *author* ignoring case. ``--no-show-signature`` keeps a
    ``log.showSignature`` setting from adding lines and verifying every commit.
    """
    origin = _origin(checkout, git_dir=git_dir, rewrites=rewrites)
    since = f"--since={days}.days"
    log = _git(checkout, "log", "--all", since, "--format=%ae", "--no-show-signature")
    # One line per commit, empty for an empty email, so the output is not stripped.
    emails = log.splitlines()
    own = author.casefold()
    return _History(
        origin=origin,
        commits=len(emails),
        own_commits=sum(1 for email in emails if email.strip().casefold() == own) if own else 0,
    )


def _origin(checkout: Path, *, git_dir: Path, rewrites: Collection[str] | None) -> str:
    """What ``git remote get-url origin`` prints, read from the config file when plain.

    *rewrites* are the user's ``insteadOf`` prefixes; None means they are unknown
    and git is always asked.
    """
    if rewrites is not None:
        configured = configured_origin(git_dir, rewrites=rewrites)
        if configured is not None:
            return configured
    return _git(checkout, "remote", "get-url", "origin").strip()


def _user_config(root: Path) -> _UserConfig:
    """The user's email and URL rewrite rules as seen from *root*, in one git call.

    The last ``user.email`` wins, as with ``git config --get``. Rewrites are None
    when a conditional include could hold rules for some repositories only.
    """
    email = ""
    prefixes: list[str] = []
    knowable = True
    for entry in _git(root, "config", "-z", "--get-regexp", _USER_CONFIG_KEYS).split("\0"):
        key, _, value = entry.partition("\n")
        if key == "user.email":
            email = value.strip()
        elif key.startswith("includeif."):
            knowable = False
        elif key:
            prefixes.append(value)
    return _UserConfig(email=email, rewrites=tuple(prefixes) if knowable else None)


def _worktree(checkout: Path) -> _Worktree:
    status = _git(checkout, "status", "--porcelain", "--untracked-files=normal")
    return _Worktree(
        uncommitted=sum(1 for line in status.splitlines() if line.strip()),
        has_workflows=_has_workflows(checkout),
    )


def _activity(checkout: Path, history: _History, worktree: _Worktree) -> RepoActivity:
    owner, name = parse_github_remote(history.origin)
    return RepoActivity(
        name=checkout.name,
        path=str(checkout),
        origin=history.origin,
        github_owner=owner,
        github_repo=name,
        commits=history.commits,
        own_commits=history.own_commits,
        uncommitted=worktree.uncommitted,
        has_workflows=worktree.has_workflows,
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
    """Unstripped output of one read-only git call, or ``""`` when it fails or times out.

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
    return result.stdout if result.returncode == 0 else ""


__all__ = [
    "RepoActivity",
    "ScanStop",
    "WorkspaceSnapshot",
    "measure_repo",
    "parse_github_remote",
    "scan_workspace",
]
