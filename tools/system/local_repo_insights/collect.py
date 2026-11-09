"""Everything the insights read from one checkout, gathered with a handful of read-only git calls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tools.system.local_repo_insights.git_read import ReadStopped, git_output, never_stopped
from tools.system.local_repo_insights.history import Commit, read_commits
from tools.system.local_repo_insights.hygiene import Hygiene, read_hygiene
from tools.system.local_repo_insights.workflows import (
    GITHUB_ACTIONS,
    CiProvider,
    WorkflowAudit,
    audit_workflows,
    ci_providers,
)
from tools.system.workspace_git_scan.git_files import common_git_dir
from tools.system.workspace_git_scan.scan import parse_github_remote

# Remote host -> a substring of the origin URL that names it.
_HOSTS: tuple[tuple[str, str], ...] = (
    ("github", "github"),
    ("gitlab", "gitlab"),
    ("bitbucket", "bitbucket"),
    ("azure", "dev.azure.com"),
    ("azure", "visualstudio.com"),
)


@dataclass(frozen=True)
class RepoFacts:
    """One checkout's recent history, CI configuration and local state."""

    name: str
    """``owner/repo`` for a GitHub remote, else the checkout's folder name."""

    path: str
    identity: str
    """Clones and worktrees of one repository share it: the GitHub name, else the git directory."""

    host: str
    """Where ``origin`` points: github, gitlab, bitbucket, azure, other, or none."""

    own_email: str
    """The user's email as git resolves it in this checkout, casefolded; empty when unset."""

    commits: tuple[Commit, ...] | None
    """None when the history could not be read in time."""

    providers: tuple[CiProvider, ...]
    workflows: WorkflowAudit | None
    """Set when the checkout has GitHub Actions workflows."""

    hygiene: Hygiene


def repo_identity(
    checkout: Path, *, timeout: float, stopped: Callable[[], bool] = never_stopped
) -> tuple[str, str, str]:
    """``(name, identity, origin)`` of ``checkout``, so clones can be folded before measuring."""
    origin = (
        git_output(checkout, "remote", "get-url", "origin", timeout=timeout, stopped=stopped) or ""
    ).strip()
    owner, repo = parse_github_remote(origin)
    if owner and repo:
        name = f"{owner}/{repo}"
        return name, name.casefold(), origin
    return checkout.name, str(common_git_dir(checkout)), origin


def collect_repo(
    checkout: Path,
    *,
    days: int,
    now: float,
    timeout: float,
    stopped: Callable[[], bool] = never_stopped,
) -> RepoFacts:
    """Read ``checkout``: identity, the user's email, ``days`` of history, CI files and local state.

    Raises ``ReadStopped`` at the next git call once ``stopped`` is true.
    """
    name, identity, origin = repo_identity(checkout, timeout=timeout, stopped=stopped)
    email = (
        git_output(checkout, "config", "--get", "user.email", timeout=timeout, stopped=stopped)
        or ""
    )
    head = git_output(
        checkout, "rev-parse", "--verify", "--quiet", "HEAD", timeout=timeout, stopped=stopped
    )
    commits = read_commits(
        checkout, days=days, timeout=timeout, include_head=head is not None, stopped=stopped
    )
    if stopped():
        raise ReadStopped
    providers = ci_providers(checkout)
    return RepoFacts(
        name=name,
        path=str(checkout),
        identity=identity,
        host=_host(origin),
        own_email=email.strip().casefold(),
        commits=tuple(commits) if commits is not None else None,
        providers=providers,
        workflows=audit_workflows(checkout) if GITHUB_ACTIONS in providers else None,
        hygiene=read_hygiene(
            checkout, common_git_dir(checkout), now=now, timeout=timeout, stopped=stopped
        ),
    )


def _host(origin: str) -> str:
    lowered = origin.casefold()
    if not lowered:
        return "none"
    for host, marker in _HOSTS:
        if marker in lowered:
            return host
    return "other"


__all__ = ["RepoFacts", "collect_repo", "repo_identity"]
