"""Conservative changed-path detection for local validation."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git(root: Path, *args: str) -> str:
    """Run Git without a shell and return its output."""
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).rstrip("\n")


def default_base(root: Path, remote: str = "origin") -> str | None:
    """Prefer the remote default branch over possibly stale local branches."""
    candidates = (f"refs/remotes/{remote}/HEAD", f"refs/remotes/{remote}/main")
    for ref in candidates:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    return None


def changed_files(root: Path, base: str | None = None, head: str | None = None) -> list[str]:
    """Include additions, deletions, both sides of renames, and local untracked files."""
    resolved = base or default_base(root)
    if resolved:
        # A missing explicit base is an error; never silently narrow to HEAD~1.
        merge_base = git(root, "merge-base", head or "HEAD", resolved)
        args = ["diff", "--name-only", "--no-renames", "-z", merge_base]
        if head:
            args.append(head)
    else:
        # Without a base, all tracked paths need coverage (initial/new repositories).
        args = (
            ["ls-tree", "--name-only", "-r", "-z", head] if head else ["ls-files", "--cached", "-z"]
        )
    paths = set(git(root, *args).split("\0"))
    if head is None:
        paths.update(git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"))
    return sorted(paths - {""})


def is_documentation(path: str) -> bool:
    """Recognize non-executable documentation; runtime prompts are source changes."""
    item = Path(path)
    if len(item.parts) == 1 and item.suffix in {".md", ".mdx"}:
        return True
    return path == "docs/docs.json" or (
        path.startswith("docs/") and item.suffix in {".md", ".mdx", ".png", ".jpg", ".jpeg", ".svg"}
    )
