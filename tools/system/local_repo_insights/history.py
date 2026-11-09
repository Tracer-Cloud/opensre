"""One repository's recent commits, read with a single ``git log``.

Only what the insights need leaves this module: author email, time, which files
changed and how much, whether the subject reads like a fix or a revert, and
which AI agents co-authored it. Commit messages and diffs are never kept.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from tools.system.local_repo_insights.git_read import git_output, never_stopped

_RECORD = "\x1e"
_FIELD = "\x1f"
_TRAILER = "\x1d"
# Author email and name, committer email, author time, the author's local
# weekday and hour, subject, and Co-authored-by trailer values.
_FORMAT = (
    "%x1e%ae%x1f%an%x1f%ce%x1f%at%x1f%ad%x1f%s%x1f"
    "%(trailers:key=Co-authored-by,valueonly,separator=%x1d)%x1f"
)
_FIELDS = 8
# Commits GitHub makes when someone presses a merge button: they repeat work
# already counted from its branch and carry the merge time, not the work's.
_GITHUB_WEB_COMMITTER = "noreply@github.com"
_FIX_SUBJECT = re.compile(
    r"^(?:fixup|squash|amend)!|\b(?:fix(?:e[sd])?|fixup|hotfix|bugfix|oops|typo|broken|flaky"
    r"|retry|revert|(?:try|trying) again)\b",
    re.IGNORECASE,
)
_REVERT_SUBJECT = re.compile(r"^revert\b", re.IGNORECASE)
_TRAILER_PARTS = re.compile(r"^(?P<name>.*?)\s*<(?P<email>[^>]*)>\s*$")
# A co-author counts as an AI agent by its address, or by its name when the
# address is a no-reply one, so a person named Claude stays a person.
_AGENT_EMAILS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Claude", re.compile(r"@anthropic\.com$")),
    ("Codex", re.compile(r"@openai\.com$")),
    ("Copilot", re.compile(r"\+copilot@users\.noreply\.github\.com$")),
    ("Cursor", re.compile(r"@cursor\.(?:com|sh)$|^cursoragent@")),
    ("Gemini", re.compile(r"gemini|google-labs-jules")),
    ("Devin", re.compile(r"devin-ai-integration")),
    ("Aider", re.compile(r"@aider\.chat$")),
)
_AGENT_NAMES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Claude", re.compile(r"\bclaude\b", re.IGNORECASE)),
    ("Codex", re.compile(r"\b(?:codex|openai)\b", re.IGNORECASE)),
    ("Copilot", re.compile(r"\bcopilot\b", re.IGNORECASE)),
    ("Cursor", re.compile(r"\bcursor\b", re.IGNORECASE)),
    ("Gemini", re.compile(r"\b(?:gemini|jules)\b", re.IGNORECASE)),
    ("Devin", re.compile(r"\bdevin\b", re.IGNORECASE)),
    ("Aider", re.compile(r"\baider\b", re.IGNORECASE)),
)
_AIDER_AUTHOR = re.compile(r"\(aider\)\s*$", re.IGNORECASE)
_LOCKFILES = frozenset(
    {
        "Cargo.lock",
        "Gemfile.lock",
        "Pipfile.lock",
        "Podfile.lock",
        "bun.lock",
        "bun.lockb",
        "composer.lock",
        "flake.lock",
        "go.sum",
        "mix.lock",
        "package-lock.json",
        "packages.lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pubspec.lock",
        "uv.lock",
        "yarn.lock",
    }
)


@dataclass(frozen=True)
class Commit:
    """One non-merge commit, reduced to the facts the insights read."""

    author_email: str
    """Casefolded, for matching against the user's configured email."""

    authored_at: int
    """Author time, Unix seconds."""

    weekday: int
    """ISO weekday (1 = Monday) in the author's own time zone."""

    hour: int
    """Hour of day in the author's own time zone."""

    fix: bool
    """The subject reads like a fix, a fixup or a retry."""

    revert: bool
    agents: tuple[str, ...]
    """AI agents among the co-authors, by family name."""

    files: tuple[str, ...]
    lines: int
    """Lines added plus deleted, lockfiles and binary files not counted."""


def read_commits(
    checkout: Path,
    *,
    days: int,
    timeout: float,
    include_head: bool = True,
    stopped: Callable[[], bool] = never_stopped,
) -> list[Commit] | None:
    """Non-merge commits of the last ``days`` days on HEAD, local and remote-tracking branches.

    ``include_head`` adds work on a detached HEAD; pass False when HEAD does not
    resolve yet (a repository with no commits), or git refuses the whole call.
    None when git failed or ran out of time. A commit reachable from several
    refs is listed once; copies a rebase left behind (same author, time and
    subject) are listed once too.
    """
    output = git_output(
        checkout,
        "log",
        *(("HEAD",) if include_head else ()),
        "--branches",
        "--remotes",
        "--no-merges",
        f"--since={days}.days",
        "--no-show-signature",
        "--date=format:%u %H",
        f"--format={_FORMAT}",
        "--numstat",
        timeout=timeout,
        stopped=stopped,
    )
    if output is None:
        return None
    commits: list[Commit] = []
    seen: set[tuple[str, str, str]] = set()
    for chunk in output.split(_RECORD)[1:]:
        parts = chunk.split(_FIELD)
        if len(parts) != _FIELDS:
            continue
        email, name, committer, at, local_time, subject, trailers, numstat = parts
        if committer.strip().casefold() == _GITHUB_WEB_COMMITTER:
            continue
        key = (email.casefold(), at, subject)
        if key in seen:
            continue
        seen.add(key)
        commit = _commit(email, name, at, local_time, subject, trailers, numstat)
        if commit is not None:
            commits.append(commit)
    return commits


def _commit(
    email: str, name: str, at: str, local_time: str, subject: str, trailers: str, numstat: str
) -> Commit | None:
    weekday, _, hour = local_time.strip().partition(" ")
    if not (at.strip().isdigit() and weekday.isdigit() and hour.isdigit()):
        return None
    files, lines = _changes(numstat)
    return Commit(
        author_email=email.strip().casefold(),
        authored_at=int(at),
        weekday=int(weekday),
        hour=int(hour),
        fix=bool(_FIX_SUBJECT.search(subject)),
        revert=bool(_REVERT_SUBJECT.search(subject)),
        agents=_agents(name, trailers),
        files=files,
        lines=lines,
    )


def _changes(numstat: str) -> tuple[tuple[str, ...], int]:
    files: list[str] = []
    lines = 0
    for row in numstat.strip().splitlines():
        added, _, rest = row.partition("\t")
        deleted, _, path = rest.partition("\t")
        if not path:
            continue
        path = _renamed_to(path)
        files.append(path)
        if PurePosixPath(path).name in _LOCKFILES:
            continue
        # Binary files report "-" for both counts.
        if added.isdigit() and deleted.isdigit():
            lines += int(added) + int(deleted)
    return tuple(files), lines


def _renamed_to(path: str) -> str:
    """The new path of a ``--numstat`` rename (``old => new`` or ``dir/{old => new}/file``)."""
    if " => " not in path:
        return path
    if "{" in path and "}" in path:
        prefix, _, rest = path.partition("{")
        inner, _, suffix = rest.partition("}")
        _old, _, new = inner.partition(" => ")
        return (prefix + new + suffix).replace("//", "/")
    return path.partition(" => ")[2]


def _agents(author_name: str, trailers: str) -> tuple[str, ...]:
    found: list[str] = []
    if _AIDER_AUTHOR.search(author_name):
        found.append("Aider")
    for value in trailers.split(_TRAILER):
        agent = _agent(value.strip())
        if agent and agent not in found:
            found.append(agent)
    return tuple(found)


def _agent(trailer: str) -> str:
    match = _TRAILER_PARTS.match(trailer)
    if match is None:
        return ""
    name, email = match.group("name"), match.group("email").strip().casefold()
    for agent, pattern in _AGENT_EMAILS:
        if pattern.search(email):
            return agent
    if email and "noreply" not in email:
        return ""
    for agent, pattern in _AGENT_NAMES:
        if pattern.search(name):
            return agent
    return ""


__all__ = ["Commit", "read_commits"]
