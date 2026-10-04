"""Loose ends in one checkout: an uninstalled commit hook, stale branches, stashes, uncommitted files."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tools.system.local_repo_insights.git_read import git_output, never_stopped

STALE_BRANCH_DAYS = 30
_SECONDS_PER_DAY = 86_400
# Hook framework -> files that configure it in the checkout.
_HOOK_CONFIGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pre-commit", (".pre-commit-config.yaml", ".pre-commit-config.yml")),
    ("husky", (".husky",)),
    ("lefthook", ("lefthook.yml", "lefthook.yaml", ".lefthook.yml", ".lefthook.yaml")),
)


@dataclass(frozen=True)
class Hygiene:
    """One checkout's local state."""

    hook_framework: str
    """``pre-commit``, ``husky`` or ``lefthook`` when the checkout configures one, else empty."""

    hook_installed: bool
    """A ``pre-commit`` hook, or a ``core.hooksPath`` folder, is in place for git to run."""

    local_branches: int
    stale_branches: int
    """Local branches whose last commit is older than ``STALE_BRANCH_DAYS``."""

    stashes: int
    uncommitted: int
    shallow: bool


def read_hygiene(
    checkout: Path,
    git_dir: Path,
    *,
    now: float,
    timeout: float,
    stopped: Callable[[], bool] = never_stopped,
) -> Hygiene:
    """Read ``checkout``'s local state; a git call that fails counts as nothing found."""
    framework = _hook_framework(checkout)
    branch_times = [
        int(value)
        for value in (
            git_output(
                checkout,
                "for-each-ref",
                "refs/heads",
                "--format=%(committerdate:unix)",
                timeout=timeout,
                stopped=stopped,
            )
            or ""
        ).split()
        if value.isdigit()
    ]
    cutoff = now - STALE_BRANCH_DAYS * _SECONDS_PER_DAY
    stashes = git_output(checkout, "stash", "list", timeout=timeout, stopped=stopped) or ""
    status = (
        git_output(
            checkout,
            "status",
            "--porcelain",
            "--untracked-files=normal",
            timeout=timeout,
            stopped=stopped,
        )
        or ""
    )
    shallow = (
        git_output(
            checkout, "rev-parse", "--is-shallow-repository", timeout=timeout, stopped=stopped
        )
        or ""
    )
    return Hygiene(
        hook_framework=framework,
        hook_installed=bool(framework)
        and _hook_installed(checkout, git_dir, timeout=timeout, stopped=stopped),
        local_branches=len(branch_times),
        stale_branches=sum(1 for at in branch_times if at < cutoff),
        stashes=sum(1 for line in stashes.splitlines() if line.strip()),
        uncommitted=sum(1 for line in status.splitlines() if line.strip()),
        shallow=shallow.strip() == "true",
    )


def _hook_framework(checkout: Path) -> str:
    for framework, names in _HOOK_CONFIGS:
        if any((checkout / name).exists() for name in names):
            return framework
    return ""


def _hook_installed(
    checkout: Path, git_dir: Path, *, timeout: float, stopped: Callable[[], bool]
) -> bool:
    """Whether git would run a commit hook here: a ``core.hooksPath`` folder or ``hooks/pre-commit``."""
    hooks_path = (
        git_output(checkout, "config", "--get", "core.hooksPath", timeout=timeout, stopped=stopped)
        or ""
    ).strip()
    if hooks_path:
        folder = Path(hooks_path).expanduser()
        return (folder if folder.is_absolute() else checkout / folder).is_dir()
    return (git_dir / "hooks" / "pre-commit").is_file()


__all__ = ["STALE_BRANCH_DAYS", "Hygiene", "read_hygiene"]
