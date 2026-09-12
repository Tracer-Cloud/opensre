"""On-demand reference files: discovered by directory, loadable only by slug."""

from __future__ import annotations

from core.agent_harness.prompts.skills import (
    load_skill_reference,
    skill_reference_names,
)


def test_analyzing_github_ci_performance_lists_metrics_reference() -> None:
    assert "metrics" in skill_reference_names("analyzing-github-ci-performance")


def test_load_skill_reference_returns_metrics_content() -> None:
    content = load_skill_reference("analyzing-github-ci-performance", "metrics")
    assert "red_hours" in content
    assert "blocked_working_minutes" in content


def test_load_skill_reference_unknown_slug_is_empty() -> None:
    assert load_skill_reference("analyzing-github-ci-performance", "no-such-reference") == ""
    assert load_skill_reference("no-such-skill", "metrics") == ""


def test_load_skill_reference_rejects_path_traversal() -> None:
    assert load_skill_reference("analyzing-github-ci-performance", "../SKILL") == ""
    assert load_skill_reference("analyzing-github-ci-performance", "references/metrics") == ""


def test_skill_without_reference_directory_has_none() -> None:
    assert skill_reference_names("fixing-github-ci") == ()
