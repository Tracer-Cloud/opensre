"""Fill ``after_tool`` menu options from the trigger tool's result.

Builders are named in skill frontmatter (``options_from``). They read the
structured payload the trigger tool already returned — no user-text scan.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_SCAN_REPOS = "local_git_scan_repos"
_EXIT_DEMO = "Exit demo"
_MAX_SCAN_CANDIDATES = 3
_MIN_CHOICE_OPTIONS = 2
_MAX_CHOICE_OPTIONS = 8


def options_from_tool_result(
    builder: str | None, details: Any, extra: Sequence[str]
) -> tuple[str, ...]:
    """Return option labels for an ``after_tool`` menu, or ``()`` when none apply."""
    labels: list[str] = []
    if builder == _SCAN_REPOS:
        labels.extend(_local_git_scan_repo_options(details))
    for item in extra:
        text = item.strip()
        if text and text not in labels:
            labels.append(text)
    if builder == _SCAN_REPOS and 0 < len(labels) < _MIN_CHOICE_OPTIONS:
        labels.append(_EXIT_DEMO)
    return tuple(labels[:_MAX_CHOICE_OPTIONS])


def _local_git_scan_repo_options(details: Any) -> list[str]:
    repos = details.get("repos") if isinstance(details, Mapping) else None
    if not isinstance(repos, list):
        return []
    candidates: list[tuple[int, str]] = []
    for raw in repos:
        if not isinstance(raw, Mapping):
            continue
        github = raw.get("github")
        if not isinstance(github, str) or not github.strip() or not raw.get("has_workflows"):
            continue
        commits = raw.get("commits")
        count = commits if isinstance(commits, int) else 0
        candidates.append((count, github.strip()))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [
        f"{name} ({count} commits, CI configured)"
        for count, name in candidates[:_MAX_SCAN_CANDIDATES]
    ]


__all__ = ["options_from_tool_result"]
