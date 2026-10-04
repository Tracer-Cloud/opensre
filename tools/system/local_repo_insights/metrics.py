"""Per-repository habits computed from the collected facts: no git calls, no file reads."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath

from tools.system.local_repo_insights.collect import RepoFacts
from tools.system.local_repo_insights.history import Commit
from tools.system.local_repo_insights.hygiene import Hygiene
from tools.system.local_repo_insights.workflows import WorkflowAudit

# A fix this soon after the user's previous commit, to a file that commit
# changed, is a follow-up fix rather than new work.
FOLLOW_UP_SECONDS = 3_600
# CI-config commits this close together form one trial-and-error run.
CI_RUN_GAP_SECONDS = 1_800
CI_RUN_MIN_COMMITS = 3
LARGE_COMMIT_LINES = 1_000
_LATE_START_HOUR = 21
_LATE_END_HOUR = 6
_WEEKEND = frozenset({6, 7})
_CI_PATH = re.compile(
    r"^(?:\.github/workflows/|\.gitlab-ci\.ya?ml$|\.circleci/|Jenkinsfile$|\.buildkite/"
    r"|azure-pipelines\.ya?ml$|bitbucket-pipelines\.ya?ml$|\.travis\.ya?ml$|\.drone\.ya?ml$"
    r"|\.woodpecker(?:\.ya?ml$|/))"
)
_TEST_PATH = re.compile(
    r"(?:^|/)(?:tests?|__tests__|specs?)/|(?:^|/)test_[^/]*$|_(?:test|spec)\.[^/]+$"
    r"|\.(?:test|spec)\.[^/]+$|[a-z0-9]Tests?\.[^/.]+$"
)
_CODE_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".clj",
        ".cpp",
        ".cs",
        ".cxx",
        ".dart",
        ".ex",
        ".exs",
        ".fs",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".jl",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".lua",
        ".m",
        ".mjs",
        ".cjs",
        ".mm",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".scala",
        ".svelte",
        ".swift",
        ".ts",
        ".tsx",
        ".vue",
    }
)


@dataclass(frozen=True)
class CiRun:
    """Consecutive CI-config commits, each within ``CI_RUN_GAP_SECONDS`` of the one before."""

    commits: int
    minutes: int
    day: str
    """UTC date of the first commit, ISO format."""

    files: tuple[str, ...]


@dataclass(frozen=True)
class RepoMetrics:
    """One repository's figures for the window; "own" means authored with the user's email."""

    name: str
    host: str
    providers: tuple[str, ...]
    """Display names of the CI services configured, GitHub Actions first."""

    history_read: bool
    commits: int
    own_commits: int
    ai_coauthored: int
    agents: Counter[str]
    follow_up_fixes: int
    reverts: int
    ci_config_commits: int
    ci_runs: tuple[CiRun, ...]
    code_commits: int
    code_commits_with_tests: int
    large_commits: int
    hours: tuple[int, ...]
    """Own commits per hour of day, in the author's time zone."""

    weekend: int
    late_night: int
    workflows: WorkflowAudit | None
    hygiene: Hygiene


def repo_metrics(facts: RepoFacts) -> RepoMetrics:
    """The figures the insights compare, from ``facts`` alone."""
    commits = list(facts.commits or ())
    own = sorted(
        (
            commit
            for commit in commits
            if facts.own_email and commit.author_email == facts.own_email
        ),
        key=lambda commit: commit.authored_at,
    )
    agents: Counter[str] = Counter(agent for commit in own for agent in commit.agents)
    code = [commit for commit in own if any(_is_code(path) for path in commit.files)]
    hours = [0] * 24
    for commit in own:
        hours[commit.hour % 24] += 1
    return RepoMetrics(
        name=facts.name,
        host=facts.host,
        providers=tuple(provider.name for provider in facts.providers),
        history_read=facts.commits is not None,
        commits=len(commits),
        own_commits=len(own),
        ai_coauthored=sum(1 for commit in own if commit.agents),
        agents=agents,
        follow_up_fixes=_follow_up_fixes(own),
        reverts=sum(1 for commit in commits if commit.revert),
        ci_config_commits=sum(1 for commit in own if _touches_ci(commit)),
        ci_runs=_ci_runs(own),
        code_commits=len(code),
        code_commits_with_tests=sum(
            1 for commit in code if any(_TEST_PATH.search(path) for path in commit.files)
        ),
        large_commits=sum(1 for commit in own if commit.lines > LARGE_COMMIT_LINES),
        hours=tuple(hours),
        weekend=sum(1 for commit in own if commit.weekday in _WEEKEND),
        late_night=sum(
            1 for commit in own if commit.hour >= _LATE_START_HOUR or commit.hour < _LATE_END_HOUR
        ),
        workflows=facts.workflows,
        hygiene=facts.hygiene,
    )


def _follow_up_fixes(own: list[Commit]) -> int:
    """Fixes to a file the user's previous commit changed, made within ``FOLLOW_UP_SECONDS`` of it."""
    count = 0
    for previous, current in zip(own, own[1:], strict=False):
        if (
            current.fix
            and current.authored_at - previous.authored_at <= FOLLOW_UP_SECONDS
            and set(current.files) & set(previous.files)
        ):
            count += 1
    return count


def _ci_runs(own: list[Commit]) -> tuple[CiRun, ...]:
    runs: list[list[Commit]] = []
    current: list[Commit] = []
    for commit in (commit for commit in own if _touches_ci(commit)):
        if current and commit.authored_at - current[-1].authored_at > CI_RUN_GAP_SECONDS:
            runs.append(current)
            current = []
        current.append(commit)
    if current:
        runs.append(current)
    return tuple(_ci_run(run) for run in runs if len(run) >= CI_RUN_MIN_COMMITS)


def _ci_run(run: list[Commit]) -> CiRun:
    files = sorted({path for commit in run for path in commit.files if _CI_PATH.match(path)})
    return CiRun(
        commits=len(run),
        minutes=round((run[-1].authored_at - run[0].authored_at) / 60),
        day=datetime.fromtimestamp(run[0].authored_at, tz=UTC).date().isoformat(),
        files=tuple(files[:2]),
    )


def _touches_ci(commit: Commit) -> bool:
    return any(_CI_PATH.match(path) for path in commit.files)


def _is_code(path: str) -> bool:
    return (
        PurePosixPath(path).suffix.lower() in _CODE_SUFFIXES
        and not _TEST_PATH.search(path)
        and not _CI_PATH.match(path)
    )


__all__ = [
    "CI_RUN_GAP_SECONDS",
    "CI_RUN_MIN_COMMITS",
    "FOLLOW_UP_SECONDS",
    "LARGE_COMMIT_LINES",
    "CiRun",
    "RepoMetrics",
    "repo_metrics",
]
