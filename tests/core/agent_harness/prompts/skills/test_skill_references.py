"""On-demand reference files: discovered by directory, loadable only by slug."""

from __future__ import annotations

from core.agent_harness.prompts.skills.loader import (
    load_skill_reference,
    skill_reference_names,
)


def test_cicd_analytics_demo_lists_metrics_reference() -> None:
    assert "metrics" in skill_reference_names("cicd-analytics-demo")


def test_load_skill_reference_returns_metrics_content() -> None:
    content = load_skill_reference("cicd-analytics-demo", "metrics")
    assert "red_hours" in content
    assert "blocked_working_minutes" in content


def test_load_skill_reference_unknown_slug_is_empty() -> None:
    assert load_skill_reference("cicd-analytics-demo", "no-such-reference") == ""
    assert load_skill_reference("no-such-skill", "metrics") == ""


def test_load_skill_reference_rejects_path_traversal() -> None:
    assert load_skill_reference("cicd-analytics-demo", "../SKILL") == ""
    assert load_skill_reference("cicd-analytics-demo", "references/metrics") == ""


def test_skill_without_reference_directory_has_none() -> None:
    assert skill_reference_names("fixing-github-ci") == ()
