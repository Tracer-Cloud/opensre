"""Tests for recurring skill scheduling contracts."""

from __future__ import annotations

import pytest

from core.agent_harness.prompts.skills.loader import clear_skills_caches, list_action_skills
from core.agent_harness.prompts.skills.schedule import (
    find_action_skill,
    is_recurring_skill,
    pin_recurring_skill,
    resolve_scheduled_skill,
    skill_revision,
    validate_skill_inputs,
)


def test_morning_report_is_recurring() -> None:
    assert is_recurring_skill("morning-report") is True


def test_non_recurring_skill_is_not_schedulable() -> None:
    for skill in list_action_skills():
        if not (skill.recurring or "").strip():
            assert is_recurring_skill(skill.name) is False
            with pytest.raises(RuntimeError, match="not marked recurring"):
                pin_recurring_skill(skill.name)
            return
    pytest.skip("no non-recurring skills in tree")


def test_resolve_scheduled_skill_pins_revision() -> None:
    skill = find_action_skill("morning-report")
    assert skill is not None
    pinned = skill_revision(skill)
    resolved = resolve_scheduled_skill("morning-report", pinned)
    assert resolved.name == "morning-report"
    assert "MORNING REPORT SKILL" in resolved.body
    assert resolved.revision == pinned


def test_resolve_scheduled_skill_rejects_missing_skill() -> None:
    with pytest.raises(RuntimeError, match="not installed"):
        resolve_scheduled_skill("missing-skill-xyz", "abc123")


def test_resolve_scheduled_skill_rejects_revision_drift() -> None:
    skill = find_action_skill("morning-report")
    assert skill is not None
    with pytest.raises(RuntimeError, match="changed since it was scheduled"):
        resolve_scheduled_skill("morning-report", "0" * 64)


def test_validate_skill_inputs_rejects_non_strings() -> None:
    with pytest.raises(ValueError, match="must be a string"):
        validate_skill_inputs({"city": 123})

    with pytest.raises(ValueError, match="keys must be strings"):
        validate_skill_inputs({1: "Paris"})


def test_skill_revision_changes_when_body_changes() -> None:
    skill = find_action_skill("morning-report")
    assert skill is not None
    before = skill_revision(skill)
    original = skill.path.read_text(encoding="utf-8")
    skill.path.write_text(original + "\n<!-- test pin -->\n", encoding="utf-8")
    clear_skills_caches()
    try:
        refreshed = find_action_skill("morning-report")
        assert refreshed is not None
        assert skill_revision(refreshed) != before
    finally:
        skill.path.write_text(original, encoding="utf-8")
        clear_skills_caches()
