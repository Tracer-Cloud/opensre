"""An analysis started at the repository pick answers the matching call once, like a direct call."""

from __future__ import annotations

from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest

import integrations.github.tools.ci_analytics.analysis as analysis_module
import integrations.github.tools.ci_analytics.prefetch as prefetch_module
import integrations.github.tools.ci_analytics.tool as tool_module
from config.constants import GH_TOKEN_ENV, GITHUB_MCP_AUTH_TOKEN_ENV, GITHUB_TOKEN_ENV
from core.tool_framework.utils import PrefetchRegistry
from integrations.github import prefetch_ci_analysis
from integrations.github.client import GitHubApiError
from integrations.github.tools.ci_analytics.collector import CollectedRuns

_OWNER, _REPO = "acme", "widget"


class _GitHub:
    """Fake Actions history: counts reads; the first ``failures_left`` reads fail."""

    def __init__(self) -> None:
        self.reads = 0
        self.failures_left = 0

    def __call__(self, _client: Any, **_kwargs: Any) -> CollectedRuns:
        self.reads += 1
        if self.failures_left:
            self.failures_left -= 1
            raise GitHubApiError("rate limited", status_code=HTTPStatus.TOO_MANY_REQUESTS)
        return CollectedRuns(
            default_branch="main", branch_runs=[], pr_runs=[], merged_prs=(), coverage_notices=[]
        )


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _GitHub:
    for name in (GITHUB_MCP_AUTH_TOKEN_ENV, GH_TOKEN_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "ghp_session")
    monkeypatch.setattr(tool_module, "snapshot_root", lambda _root=None: tmp_path)
    fake = _GitHub()
    monkeypatch.setattr(analysis_module, "collect_runs", fake)
    return fake


@pytest.fixture
def snapshots(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """The ``now`` of every snapshot the tool writes."""
    written: list[datetime] = []
    real_write = tool_module.write_snapshot

    def write(root: Path, owner: str, repo: str, now: datetime, payload: dict[str, Any]) -> Path:
        written.append(now)
        return real_write(root, owner, repo, now, payload)

    monkeypatch.setattr(tool_module, "write_snapshot", write)
    return written


@pytest.fixture
def run_errors(monkeypatch: pytest.MonkeyPatch) -> list[Exception]:
    reported: list[Exception] = []

    def report(exc: Exception, **_kwargs: Any) -> None:
        reported.append(exc)

    monkeypatch.setattr(tool_module, "report_run_error", report)
    return reported


def _analyze(**kwargs: Any) -> dict[str, Any]:
    return tool_module.analyze_github_ci_reliability(
        **{"owner": _OWNER, "repo": _REPO, "days": 30, **kwargs}
    )


def test_a_prefetched_analysis_answers_the_matching_call_once_like_a_direct_call(
    github: _GitHub, snapshots: list[datetime]
) -> None:
    # Arrange: a direct call to compare against, then the prefetch.
    direct = _analyze()
    assert prefetch_ci_analysis(_OWNER, _REPO, resolved_integrations={})

    # Act
    joined = _analyze()
    again = _analyze()

    # Assert: one read per analysis, one snapshot per call, the same result.
    assert github.reads == 3
    assert len(snapshots) == 3
    assert joined == direct
    assert again == direct


def test_another_window_token_or_a_stale_prefetch_reads_live(
    github: _GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: prefetches held by a registry whose clock the test moves.
    clock = _Clock()
    registry: PrefetchRegistry[Any, Any] = PrefetchRegistry(
        name="test", max_age_seconds=300.0, max_entries=4, clock=clock
    )
    monkeypatch.setattr(prefetch_module, "_ANALYSES", registry)
    assert prefetch_ci_analysis(_OWNER, _REPO, resolved_integrations={})

    for entry in list(registry._entries.values()):
        assert entry.done.wait(5.0)

    # Act: another window and another token each read live; so does a stale prefetch.
    _analyze(days=14)
    _analyze(github_token="ghp_other")
    clock.now = 301.0
    _analyze()

    # Assert: the prefetch read plus three live reads.
    assert github.reads == 4


def test_a_failed_prefetch_leaves_the_call_to_read_and_report_on_its_own(
    github: _GitHub, run_errors: list[Exception], snapshots: list[datetime]
) -> None:
    # Arrange: the background read fails; the call's own read succeeds.
    github.failures_left = 1
    assert prefetch_ci_analysis(_OWNER, _REPO, resolved_integrations={})

    # Act
    result = _analyze()

    # Assert: nothing reported for the prefetch, one snapshot from the live read.
    assert result["success"] is True
    assert github.reads == 2
    assert run_errors == []
    assert len(snapshots) == 1


def test_no_token_starts_no_prefetch(github: _GitHub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GITHUB_TOKEN_ENV)

    assert not prefetch_ci_analysis(_OWNER, _REPO, resolved_integrations={})
    assert github.reads == 0
