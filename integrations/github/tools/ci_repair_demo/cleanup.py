"""Write demo evidence for the approved repository and drop its scheduled task."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from integrations.github.tools.ci_repair_demo.seed import DemoRefused, github_component

_OUTCOMES = frozenset({"success", "failed", "blocked"})


def results_directory() -> Path:
    """Durable demo evidence, shared with the onboarding helpers."""
    return Path.home() / ".opensre" / "demo-results"


def split_repo(repo: str) -> tuple[str, str]:
    """``owner/repo``, including a name the older suffixed-only helper rejected."""
    owner, separator, name = repo.strip().partition("/")
    if not separator or "/" in name:
        raise DemoRefused("Expected owner/repo.")
    return github_component(owner), github_component(name)


def write_evidence(
    *,
    repo: str,
    pr_number: int,
    loop_id: str,
    outcome: str,
    failed_run_id: int = 0,
    fix_commit: str = "",
    passing_run_id: int = 0,
    directory: Path | None = None,
) -> Path:
    """Save the observed outcome. The GitHub repository is not deleted."""
    owner, name = split_repo(repo)
    if outcome not in _OUTCOMES:
        raise DemoRefused("outcome must be success, failed, or blocked.")
    if pr_number < 1:
        raise DemoRefused("Record the pull request number.")
    if not loop_id.strip():
        raise DemoRefused("Record the scheduled task id.")
    if outcome == "success" and not (failed_run_id and fix_commit.strip() and passing_run_id):
        raise DemoRefused(
            "Successful repair evidence requires failed run, fix commit, and passing run."
        )
    full_name = f"{owner}/{name}"
    folder = directory or results_directory()
    path = folder / f"ci-repair-demo-{date.today().isoformat()}-{owner}--{name}.md"
    content = (
        f"# CI repair demo\n\n"
        f"- Repository: {full_name}\n"
        f"- PR: https://github.com/{full_name}/pull/{pr_number}\n"
        f"- Outcome: {outcome}\n"
        f"- Failed run: {failed_run_id or 'none'}\n"
        f"- Loop: {loop_id.strip()}\n"
        f"- Fix commit: {fix_commit.strip() or 'none'}\n"
        f"- Passing run: {passing_run_id or 'none'}\n"
        "- Repository retained.\n"
    )
    _atomic_write(path, content)
    return path


def loop_still_listed(loop_id: str, tasks: list[Any]) -> bool:
    """Whether a schedule listing still contains this task id."""
    wanted = loop_id.strip()
    return any(getattr(task, "id", None) == wanted for task in tasks)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(text)
            stream.flush()
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
