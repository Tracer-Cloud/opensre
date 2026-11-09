"""Pick the user's repositories, read them within a time budget, and rank what stands out."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from core.tool import report_run_error
from tools.system.local_repo_insights.collect import RepoFacts, collect_repo
from tools.system.local_repo_insights.git_read import ReadStopped
from tools.system.local_repo_insights.insights import Insight, rank_insights
from tools.system.local_repo_insights.metrics import RepoMetrics, repo_metrics
from tools.system.workspace_git_scan import (
    MACOS_PRIVACY_PROTECTED,
    RepoActivity,
    ScanStop,
    workspace_snapshot,
)
from tools.system.workspace_git_scan.git_files import common_git_dir

logger = logging.getLogger(__name__)

TOOL_NAME = "analyze_local_repositories"
MAX_REPOSITORIES = 8
_BUDGET_SECONDS = 15.0
_GIT_TIMEOUT_SECONDS = 10.0
_WORKERS = 4
_POLL_SECONDS = 0.05
_PROGRESS_INTERVAL_SECONDS = 3.0
_PROGRESS = "Reading recent history: {} of {} repositories done"


class AnalysisStop(StrEnum):
    """Why the analysis ended before reading every chosen repository."""

    CANCELLED = "cancelled"
    TIME_BUDGET = "time_budget"


@dataclass(frozen=True)
class LocalAnalysis:
    """The repositories read, what stands out across them, and what limited the reading."""

    days: int
    repos: tuple[RepoMetrics, ...] = ()
    insights: tuple[Insight, ...] = ()
    notices: tuple[str, ...] = ()
    skipped_protected: tuple[str, ...] = ()
    """macOS privacy-protected folders the workspace scan did not enter."""

    stop_reason: AnalysisStop | None = None
    unreadable: int = 0
    """Chosen checkouts whose reading raised; each was reported as a warning."""

    @property
    def own_commits(self) -> int:
        return sum(repo.own_commits for repo in self.repos)

    @property
    def commits(self) -> int:
        return sum(repo.commits for repo in self.repos)


@dataclass(frozen=True)
class _Candidates:
    checkouts: tuple[Path, ...]
    skipped_protected: tuple[str, ...] = ()
    cancelled: bool = False
    missing_root: str = ""


def analyze_repositories(
    *,
    days: int,
    paths: Sequence[str] = (),
    repository: str = "",
    root: str | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_progress: Callable[[str], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> LocalAnalysis:
    """Read up to ``MAX_REPOSITORIES`` checkouts and rank their insights.

    ``paths`` are checkouts the caller already found; without them the
    workspace under ``root`` (default: home) is scanned and the user's most
    active repositories are read. ``repository`` (``owner/repo`` or a path) is
    read first when present. The reading stops on cancel or after its time
    budget, keeping what finished.
    """
    candidates = _candidates(
        paths, repository, root, days, should_stop=should_stop, on_progress=on_progress
    )
    if candidates.cancelled:
        return LocalAnalysis(days=days, stop_reason=AnalysisStop.CANCELLED)
    # The scan has its own budget; the reading gets a full one of its own.
    facts, stop, unreadable = _collect(
        candidates.checkouts,
        days=days,
        deadline=clock() + _BUDGET_SECONDS,
        should_stop=should_stop,
        on_progress=on_progress,
        clock=clock,
    )
    if stop is AnalysisStop.CANCELLED:
        return LocalAnalysis(days=days, stop_reason=stop)
    facts = _lead_with(_fold_clones(facts), repository)
    repos = tuple(repo_metrics(item) for item in facts)
    return LocalAnalysis(
        days=days,
        repos=repos,
        insights=tuple(rank_insights(repos, days=days)),
        notices=tuple(_notices(facts, repos, repository, candidates, stop, unreadable)),
        skipped_protected=candidates.skipped_protected,
        stop_reason=stop,
        unreadable=unreadable,
    )


def _candidates(
    paths: Sequence[str],
    repository: str,
    root: str | None,
    days: int,
    *,
    should_stop: Callable[[], bool] | None,
    on_progress: Callable[[str], None] | None,
) -> _Candidates:
    focus = _as_checkout(repository)
    if paths:
        given = [checkout for raw in paths if (checkout := _as_checkout(raw)) is not None]
        return _Candidates(checkouts=_distinct([*([focus] if focus else []), *given]))
    snapshot = workspace_snapshot(root, days, should_stop=should_stop, on_progress=on_progress)
    if snapshot is None:
        return _Candidates(checkouts=(), missing_root=str(root or ""))
    if snapshot.stop_reason is ScanStop.CANCELLED:
        return _Candidates(checkouts=(), cancelled=True)
    skipped = tuple(path for path in snapshot.skipped if Path(path).name in MACOS_PRIVACY_PROTECTED)
    wanted = repository.strip().casefold()
    ranked = sorted(snapshot.repos, key=lambda repo: (-repo.own_commits, -repo.commits))
    focused = [repo for repo in ranked if wanted and _matches(repo, wanted)]
    active = [repo for repo in ranked if repo.commits and repo not in focused]
    chosen = [Path(repo.path) for repo in (*focused, *active)]
    if focus is not None:
        chosen.insert(0, focus)
    return _Candidates(checkouts=_distinct(chosen), skipped_protected=skipped)


def _matches(repo: RepoActivity, wanted: str) -> bool:
    return wanted in {repo.github_full_name.casefold(), repo.path.casefold(), repo.name.casefold()}


def _as_checkout(raw: str) -> Path | None:
    """``raw`` as a local checkout path, or None when it names no git checkout."""
    text = raw.strip()
    if not text or not (text.startswith(("/", "~", ".")) or "\\" in text):
        return None
    path = Path(text).expanduser()
    try:
        return path.resolve() if (path / ".git").exists() else None
    except OSError:
        return None


def _distinct(checkouts: Sequence[Path]) -> tuple[Path, ...]:
    """``checkouts`` without a second worktree of one repository, at most ``MAX_REPOSITORIES``."""
    seen: set[Path] = set()
    kept: list[Path] = []
    for checkout in checkouts:
        shared = common_git_dir(checkout)
        if shared in seen:
            continue
        seen.add(shared)
        kept.append(checkout)
        if len(kept) == MAX_REPOSITORIES:
            break
    return tuple(kept)


def _collect(
    checkouts: Sequence[Path],
    *,
    days: int,
    deadline: float,
    should_stop: Callable[[], bool] | None,
    on_progress: Callable[[str], None] | None,
    clock: Callable[[], float],
) -> tuple[list[RepoFacts], AnalysisStop | None, int]:
    """Read ``checkouts`` concurrently until done, cancelled, or past ``deadline``; keep their order.

    Also returns how many checkouts could not be read.
    """
    if not checkouts:
        return [], None, 0
    now = time.time()
    results: dict[int, RepoFacts] = {}
    unreadable = 0
    stop: AnalysisStop | None = None
    next_progress = clock() + _PROGRESS_INTERVAL_SECONDS
    # Set when this read ends; a checkout still being read starts no further git call.
    ended = threading.Event()
    pool = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="local-insights")
    try:
        futures: dict[Future[RepoFacts], int] = {
            pool.submit(
                collect_repo,
                checkout,
                days=days,
                now=now,
                timeout=_GIT_TIMEOUT_SECONDS,
                stopped=ended.is_set,
            ): index
            for index, checkout in enumerate(checkouts)
        }
        pending = set(futures)
        while pending:
            if should_stop is not None and should_stop():
                stop = AnalysisStop.CANCELLED
                break
            if clock() >= deadline:
                stop = AnalysisStop.TIME_BUDGET
                break
            done, pending = wait(pending, timeout=_POLL_SECONDS, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    results[futures[future]] = future.result()
                except ReadStopped:
                    continue
                except Exception as exc:  # one unreadable checkout must not end the analysis
                    unreadable += 1
                    report_run_error(
                        exc,
                        tool_name=TOOL_NAME,
                        source="system",
                        component="local_repo_insights.analysis._collect",
                        severity="warning",
                        logger=logger,
                    )
            if on_progress is not None and clock() >= next_progress:
                next_progress = clock() + _PROGRESS_INTERVAL_SECONDS
                on_progress(_PROGRESS.format(len(results), len(checkouts)))
    finally:
        # A git call already running ends within its own timeout; nothing waits for it.
        ended.set()
        pool.shutdown(wait=False, cancel_futures=True)
    return [results[index] for index in sorted(results)], stop, unreadable


def _fold_clones(facts: Sequence[RepoFacts]) -> list[RepoFacts]:
    """Keep the first checkout of each repository; separate clones share their history."""
    seen: set[str] = set()
    kept: list[RepoFacts] = []
    for item in facts:
        if item.identity in seen:
            continue
        seen.add(item.identity)
        kept.append(item)
    return kept


def _lead_with(facts: list[RepoFacts], repository: str) -> list[RepoFacts]:
    """``facts`` with the repository the user chose, by ``owner/repo`` or path, first."""
    wanted = repository.strip().casefold()
    if not wanted:
        return facts
    first = [item for item in facts if wanted in {item.name.casefold(), item.path.casefold()}]
    return [*first, *(item for item in facts if item not in first)]


def _notices(
    facts: Sequence[RepoFacts],
    repos: Sequence[RepoMetrics],
    repository: str,
    candidates: _Candidates,
    stop: AnalysisStop | None,
    unreadable: int,
) -> list[str]:
    notices: list[str] = []
    if unreadable:
        notices.append(f"{unreadable} repositories couldn't be read, so they are left out.")
    if candidates.missing_root:
        notices.append(f"{candidates.missing_root} is not a folder, so nothing was scanned.")
    for item in facts:
        if item.commits is None:
            notices.append(
                f"The history of {item.name} couldn't be read in time, so its commits are left out."
            )
        elif item.hygiene.shallow:
            notices.append(f"{item.name} is a shallow clone, so its history may be incomplete.")
    if facts and not any(item.own_email for item in facts):
        notices.append(
            "git has no user.email set here, so your own commits can't be told apart; "
            "the habit insights are left out."
        )
    wanted = repository.strip()
    if (
        wanted
        and _as_checkout(wanted) is None
        and not any(repo.name.casefold() == wanted.casefold() for repo in repos)
    ):
        notices.append(
            f"No local checkout of {wanted} was found, so these insights cover your other "
            "repositories."
        )
    if stop is AnalysisStop.TIME_BUDGET:
        notices.append(
            "Reading stopped at its time limit; the most active repositories were read first."
        )
    return notices


__all__ = ["MAX_REPOSITORIES", "TOOL_NAME", "AnalysisStop", "LocalAnalysis", "analyze_repositories"]
