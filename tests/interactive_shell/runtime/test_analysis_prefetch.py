"""Menu answers start the analysis skill's reads only while that skill is active."""

from __future__ import annotations

from typing import Any

import pytest

import surfaces.interactive_shell.runtime.startup.analysis_prefetch as analysis_prefetch
from config.constants.skills import (
    ANALYZE_REPO_OPTION,
    ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
    DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME,
)
from surfaces.interactive_shell.session import Session


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def scan(**_kwargs: Any) -> bool:
        calls.append("scan")
        return True

    def analysis(owner: str, repo: str, **_kwargs: Any) -> bool:
        calls.append(f"analysis {owner}/{repo}")
        return True

    monkeypatch.setattr(analysis_prefetch, "is_test_run", lambda: False)
    monkeypatch.setattr(analysis_prefetch, "prefetch_workspace_scan", scan)
    monkeypatch.setattr(analysis_prefetch, "prefetch_ci_analysis", analysis)
    monkeypatch.setattr(analysis_prefetch, "resolve_and_cache_integrations", lambda _s: {})
    return calls


@pytest.mark.parametrize(
    ("skill", "picked", "expected"),
    [
        (ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME, ANALYZE_REPO_OPTION, ["scan"]),
        (ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME, "acme/one", ["analysis acme/one"]),
        (ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME, "Schedule a daily check", []),
        (ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME, "acme/one; rm -rf /", []),
        (DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME, "acme/one", []),
        (None, ANALYZE_REPO_OPTION, []),
    ],
    ids=["demo-pick", "repository", "other-answer", "not-a-repository", "other-skill", "no-skill"],
)
def test_only_the_analysis_skill_answers_start_reads(
    started: list[str], skill: str | None, picked: str, expected: list[str]
) -> None:
    session = Session()
    session.active_skill = skill

    analysis_prefetch.prefetch_after_menu_answer(session, picked)

    assert started == expected
