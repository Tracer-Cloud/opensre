"""Turn repository figures into ranked insights: a fact for the user, a summary for telemetry.

Each insight kind has a threshold below which it is left out, so the report only
leads with what stands out. The order is ``LocalInsightKind``'s: what fails
today first, then CI pain, then habits, and the rhythm line last. A ``fact``
may name repositories and workflow files; a ``summary`` never does.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from config.constants.local_insights import LOCAL_INSIGHT_LABELS, LocalInsightKind
from tools.system.local_repo_insights.hygiene import STALE_BRANCH_DAYS
from tools.system.local_repo_insights.metrics import LARGE_COMMIT_LINES, RepoMetrics
from tools.system.local_repo_insights.workflows import GITHUB_ACTIONS

_MIN_OWN_COMMITS = 10
_MIN_FOLLOW_UP_FIXES = 3
_MIN_FOLLOW_UP_SHARE = 0.05
_MIN_AI_SHARE = 0.2
_MIN_CODE_COMMITS = 10
_LOW_TEST_SHARE = 0.4
_HIGH_TEST_SHARE = 0.7
_MIN_ACTIVE_COMMITS = 5
_MIN_REVERTS = 2
_MIN_STALE_BRANCHES = 10
_MIN_STASHES = 3
_MIN_UNCOMMITTED = 50
_MIN_LARGE_COMMITS = 3
_MIN_LARGE_SHARE = 0.1
# Habits read from a share of the user's commits need this many to mean anything.
_MIN_HABIT_COMMITS = 20
_MAX_NAMES = 3


@dataclass(frozen=True)
class Insight:
    """One finding: its kind, the report label, what to tell the user, and what to record."""

    kind: LocalInsightKind
    fact: str
    summary: str

    @property
    def label(self) -> str:
        return LOCAL_INSIGHT_LABELS[self.kind]


def rank_insights(repos: Sequence[RepoMetrics], *, days: int) -> list[Insight]:
    """The insights ``repos`` support, most pressing first, rhythm last."""
    builders: tuple[Callable[[Sequence[RepoMetrics], int], Insight | None], ...] = (
        _retired_actions,
        _follow_up_fixes,
        _ci_trial_and_error,
        _ai_pairing,
        _workflow_hygiene,
        _no_ci,
        _tests_with_code,
        _hooks_not_installed,
        _reverts,
        _loose_ends,
        _large_commits,
        _other_ci,
        _rhythm,
    )
    return [insight for build in builders if (insight := build(repos, days)) is not None]


def _retired_actions(repos: Sequence[RepoMetrics], _days: int) -> Insight | None:
    retired = {
        repo.name: repo.workflows.retired_action_refs
        for repo in repos
        if repo.workflows is not None and repo.workflows.retired_action_refs
    }
    if not retired:
        return None
    affected = [repo for repo in repos if repo.name in retired]
    refs = sorted({ref for repo_refs in retired.values() for ref in repo_refs})
    them = "it" if len(refs) == 1 else "them"
    if len(affected) == 1:
        subject = f"{affected[0].name} still uses"
    else:
        subject = f"{len(affected)} repositories ({_names(affected)}) still use"
    return Insight(
        kind=LocalInsightKind.RETIRED_ACTIONS,
        fact=f"{subject} {_join(refs)}, which GitHub has retired, so the steps that use {them} fail.",
        summary=f"{_repositories(len(affected))} using retired actions ({_join(refs)})",
    )


def _follow_up_fixes(repos: Sequence[RepoMetrics], _days: int) -> Insight | None:
    own = sum(repo.own_commits for repo in repos)
    fixes = sum(repo.follow_up_fixes for repo in repos)
    if own < _MIN_OWN_COMMITS or fixes < _MIN_FOLLOW_UP_FIXES or fixes / own < _MIN_FOLLOW_UP_SHARE:
        return None
    share = _pct(fixes, own)
    with_fixes = [repo for repo in repos if repo.follow_up_fixes]
    top = max(with_fixes, key=lambda repo: repo.follow_up_fixes)
    where = ""
    if len(with_fixes) == 1:
        where = f" in {top.name}"
    elif top.follow_up_fixes * 2 >= fixes:
        where = f", {top.follow_up_fixes} of them in {top.name}"
    return Insight(
        kind=LocalInsightKind.FOLLOW_UP_FIXES,
        fact=(
            f"{_n(fixes)} of your {_n(own)} commits ({share}) fixed files you had changed "
            f"less than an hour earlier{where}."
        ),
        summary=(
            f"{share} of own commits ({fixes} of {own}) fixed files changed less than an hour earlier"
        ),
    )


def _ci_trial_and_error(repos: Sequence[RepoMetrics], days: int) -> Insight | None:
    runs = [(repo, run) for repo in repos for run in repo.ci_runs]
    if not runs:
        return None
    repo, longest = max(runs, key=lambda pair: (pair[1].commits, -pair[1].minutes))
    span = "within a minute" if longest.minutes < 1 else f"within {longest.minutes} minutes"
    files = f" to {_join(list(longest.files))}" if longest.files else ""
    tail = f"; that happened {len(runs)} times in {days} days" if len(runs) > 1 else ""
    return Insight(
        kind=LocalInsightKind.CI_TRIAL_AND_ERROR,
        fact=(
            f"On {longest.day} you made {longest.commits} commits{files} in {repo.name} "
            f"{span}{tail}."
        ),
        summary=(
            f"{_count(len(runs), 'burst')} of 3 or more CI-config commits within 30 minutes; "
            f"longest {longest.commits} commits in {longest.minutes} minutes"
        ),
    )


def _ai_pairing(repos: Sequence[RepoMetrics], _days: int) -> Insight | None:
    own = sum(repo.own_commits for repo in repos)
    paired = sum(repo.ai_coauthored for repo in repos)
    if own < _MIN_OWN_COMMITS or paired / own < _MIN_AI_SHARE:
        return None
    agents: Counter[str] = Counter()
    for repo in repos:
        agents.update(repo.agents)
    breakdown = ", ".join(f"{agent} {_n(count)}" for agent, count in agents.most_common(3))
    text = (
        f"{_n(paired)} of your {_n(own)} commits ({_pct(paired, own)}) were co-authored by an "
        f"AI agent ({breakdown})."
    )
    return Insight(kind=LocalInsightKind.AI_PAIRING, fact=text, summary=text.rstrip("."))


def _workflow_hygiene(repos: Sequence[RepoMetrics], _days: int) -> Insight | None:
    audited = [repo for repo in repos if repo.workflows and repo.workflows.files]
    if not audited:
        return None
    audits = [repo.workflows for repo in audited if repo.workflows is not None]
    refs = sum(audit.action_refs for audit in audits)
    unpinned = sum(audit.unpinned_action_refs for audit in audits)
    jobs = sum(audit.jobs for audit in audits)
    no_timeout = sum(audit.jobs_without_timeout for audit in audits)
    no_concurrency = sum(audit.runs_without_concurrency for audit in audits)
    parts: list[str] = []
    if unpinned:
        verb = "isn't" if unpinned == 1 else "aren't"
        parts.append(
            f"{_n(unpinned)} of {_count(refs, 'action reference')} {verb} pinned to a commit SHA"
        )
    if no_timeout:
        parts.append(
            f"{_n(no_timeout)} of {_count(jobs, 'job')} set no timeout "
            "(a stuck job runs for up to 6 hours)"
        )
    if no_concurrency:
        verb = "has" if no_concurrency == 1 else "have"
        parts.append(
            f"{_count(no_concurrency, 'workflow')} {verb} no concurrency group "
            "(a new push leaves the previous run going)"
        )
    if not parts:
        return None
    scope = (
        f"In {audited[0].name}"
        if len(audited) == 1
        else f"Across your {len(audited)} repositories with GitHub Actions"
    )
    return Insight(
        kind=LocalInsightKind.WORKFLOW_HYGIENE,
        fact=f"{scope}, {_clauses(parts)}.",
        summary=_clauses(parts),
    )


def _no_ci(repos: Sequence[RepoMetrics], days: int) -> Insight | None:
    bare = [
        repo for repo in repos if not repo.providers and repo.own_commits >= _MIN_ACTIVE_COMMITS
    ]
    if not bare:
        return None
    unchecked = sum(repo.own_commits for repo in bare)
    if len(bare) == 1:
        fact = (
            f"{bare[0].name} has no CI, so the {_n(unchecked)} commits you made there in the last "
            f"{days} days went in without automated checks."
        )
    else:
        fact = (
            f"{len(bare)} of your active repositories have no CI ({_names(bare)}), so "
            f"{_n(unchecked)} of your commits went in without automated checks."
        )
    return Insight(
        kind=LocalInsightKind.NO_CI,
        fact=fact,
        summary=f"{_repositories(len(bare))} without CI ({unchecked} own commits)",
    )


def _tests_with_code(repos: Sequence[RepoMetrics], _days: int) -> Insight | None:
    eligible = [repo for repo in repos if repo.code_commits >= _MIN_CODE_COMMITS]
    if not eligible:
        return None

    def share(repo: RepoMetrics) -> float:
        return repo.code_commits_with_tests / repo.code_commits

    low = min(eligible, key=share)
    high = max(eligible, key=share)
    if share(low) <= _LOW_TEST_SHARE:
        fact = (
            f"In {low.name}, {_n(low.code_commits_with_tests)} of {_n(low.code_commits)} code "
            f"changes touched a test"
        )
        summary = f"lowest test co-change {_pct(low.code_commits_with_tests, low.code_commits)}"
        if high is not low and share(high) >= _HIGH_TEST_SHARE:
            high_share = _pct(high.code_commits_with_tests, high.code_commits)
            fact += f"; in {high.name} it's {high_share}"
            summary += f", highest {high_share}"
        return Insight(kind=LocalInsightKind.TESTS_WITH_CODE, fact=f"{fact}.", summary=summary)
    if share(low) >= _HIGH_TEST_SHARE:
        with_tests = sum(repo.code_commits_with_tests for repo in eligible)
        code = sum(repo.code_commits for repo in eligible)
        text = (
            f"{_n(with_tests)} of your {_n(code)} code changes ({_pct(with_tests, code)}) "
            f"came with test changes."
        )
        return Insight(kind=LocalInsightKind.TESTS_WITH_CODE, fact=text, summary=text.rstrip("."))
    return None


def _hooks_not_installed(repos: Sequence[RepoMetrics], _days: int) -> Insight | None:
    gaps = [
        repo for repo in repos if repo.hygiene.hook_framework and not repo.hygiene.hook_installed
    ]
    if not gaps:
        return None
    if len(gaps) == 1:
        fact = (
            f"{gaps[0].name} has a {gaps[0].hygiene.hook_framework} config, but its git hook isn't "
            f"installed in this checkout, so those checks don't run when you commit."
        )
    else:
        fact = (
            f"{len(gaps)} repositories ({_names(gaps)}) configure commit hooks that aren't "
            f"installed in these checkouts, so those checks don't run when you commit."
        )
    return Insight(
        kind=LocalInsightKind.HOOKS_NOT_INSTALLED,
        fact=fact,
        summary=f"{_repositories(len(gaps))} with commit hooks configured but not installed",
    )


def _reverts(repos: Sequence[RepoMetrics], days: int) -> Insight | None:
    total = sum(repo.reverts for repo in repos)
    if total < _MIN_REVERTS:
        return None
    with_reverts = [repo for repo in repos if repo.reverts]
    top = max(with_reverts, key=lambda repo: repo.reverts)
    where = (
        f" in {top.name}" if len(with_reverts) == 1 else f", {top.reverts} of them in {top.name}"
    )
    return Insight(
        kind=LocalInsightKind.REVERTS,
        fact=f"{_n(total)} commits in the last {days} days were reverts{where}.",
        summary=f"{total} reverts",
    )


def _loose_ends(repos: Sequence[RepoMetrics], _days: int) -> Insight | None:
    stale = sum(repo.hygiene.stale_branches for repo in repos)
    stashes = sum(repo.hygiene.stashes for repo in repos)
    uncommitted = sum(repo.hygiene.uncommitted for repo in repos)
    parts: list[str] = []
    if stale >= _MIN_STALE_BRANCHES:
        parts.append(f"{_n(stale)} local branches haven't changed in {STALE_BRANCH_DAYS} days")
    if stashes >= _MIN_STASHES:
        parts.append(f"{_n(stashes)} stashes are parked")
    if uncommitted >= _MIN_UNCOMMITTED:
        parts.append(f"{_n(uncommitted)} files are uncommitted")
    if not parts:
        return None
    text = _clauses(parts)
    return Insight(
        kind=LocalInsightKind.LOOSE_ENDS,
        fact=f"{text[0].upper()}{text[1:]} across these repositories.",
        summary=text,
    )


def _large_commits(repos: Sequence[RepoMetrics], _days: int) -> Insight | None:
    own = sum(repo.own_commits for repo in repos)
    large = sum(repo.large_commits for repo in repos)
    if own < _MIN_HABIT_COMMITS or large < _MIN_LARGE_COMMITS or large / own < _MIN_LARGE_SHARE:
        return None
    text = (
        f"{_n(large)} of your {_n(own)} commits ({_pct(large, own)}) changed more than "
        f"{_n(LARGE_COMMIT_LINES)} lines, lockfiles not counted."
    )
    return Insight(kind=LocalInsightKind.LARGE_COMMITS, fact=text, summary=text.rstrip("."))


def _other_ci(repos: Sequence[RepoMetrics], _days: int) -> Insight | None:
    others = [
        repo for repo in repos if repo.providers and GITHUB_ACTIONS.name not in repo.providers
    ]
    if not others:
        return None
    providers = sorted({provider for repo in others for provider in repo.providers})
    verb = "runs" if len(others) == 1 else "run"
    return Insight(
        kind=LocalInsightKind.OTHER_CI,
        fact=f"{_names(others)} {verb} CI on {_join(providers)}.",
        summary=f"{_repositories(len(others))} on {_join(providers)}",
    )


def _rhythm(repos: Sequence[RepoMetrics], _days: int) -> Insight | None:
    own = sum(repo.own_commits for repo in repos)
    if own < _MIN_HABIT_COMMITS:
        return None
    hours = [sum(repo.hours[hour] for repo in repos) for hour in range(24)]
    start = max(range(24), key=lambda hour: (hours[hour] + hours[(hour + 1) % 24], -hour))
    weekend = sum(repo.weekend for repo in repos)
    text = (
        f"Your busiest hours are {start:02d}:00–{(start + 2) % 24:02d}:00, and "
        f"{_pct(weekend, own)} of your commits land on weekends."
    )
    return Insight(kind=LocalInsightKind.RHYTHM, fact=text, summary=text.rstrip("."))


def _pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "0%"


def _n(value: int) -> str:
    return f"{value:,}"


def _count(value: int, noun: str, plural: str = "") -> str:
    """``value`` with ``noun``, plural unless it is one: "1 job", "3 jobs"."""
    return f"{value:,} {noun}" if value == 1 else f"{value:,} {plural or noun + 's'}"


def _repositories(value: int) -> str:
    return _count(value, "repository", "repositories")


def _join(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _clauses(parts: Sequence[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


def _names(repos: Sequence[RepoMetrics]) -> str:
    names = [repo.name for repo in repos[:_MAX_NAMES]]
    more = len(repos) - len(names)
    return _join([*names, f"{more} more"] if more else names)


__all__ = ["Insight", "rank_insights"]
