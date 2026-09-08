"""Discover local git repositories and their recent activity."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

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
_GIT_TIMEOUT_SECONDS = 10
_GITHUB_REMOTE_RE = re.compile(
    r"(?:github\.com[:/])(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$", re.IGNORECASE
)


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

    @property
    def total_commits(self) -> int:
        return sum(repo.commits for repo in self.repos)

    @property
    def total_uncommitted(self) -> int:
        return sum(repo.uncommitted for repo in self.repos)

    @property
    def total_own_commits(self) -> int:
        return sum(repo.own_commits for repo in self.repos)


def scan_workspace(
    root: Path,
    *,
    days: int = 30,
    max_depth: int = 4,
    max_repos: int = 200,
) -> WorkspaceSnapshot:
    """Walk *root* for git checkouts and measure each one, most active first."""
    repos: list[RepoActivity] = []
    truncated = False
    author = _git(root, "config", "--get", "user.email")
    for repo_dir in _git_dirs(root, max_depth=max_depth):
        if len(repos) >= max_repos:
            truncated = True
            break
        repos.append(measure_repo(repo_dir, days=days, author=author))
    merged = _fold_clones(repos)
    merged.sort(key=lambda repo: (-repo.commits, repo.name.lower()))
    return WorkspaceSnapshot(root=str(root), days=days, repos=tuple(merged), truncated=truncated)


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


def _git_dirs(root: Path, *, max_depth: int) -> list[Path]:
    """Directories containing ``.git`` up to *max_depth* below *root*, never descending into one."""
    found: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
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
                if entry.is_dir(follow_symlinks=False):
                    stack.append((Path(entry.path), depth + 1))
            except OSError:
                continue
    return sorted(found)


def _has_workflows(repo_dir: Path) -> bool:
    workflows = repo_dir / ".github" / "workflows"
    return any(path.is_file() for pattern in _WORKFLOW_GLOBS for path in workflows.glob(pattern))


def _git(repo_dir: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


__all__ = [
    "RepoActivity",
    "WorkspaceSnapshot",
    "measure_repo",
    "parse_github_remote",
    "scan_workspace",
]
