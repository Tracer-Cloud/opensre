"""The repository question a demo needs is read from the skill that declares it."""

from __future__ import annotations

from core.agent_harness.spi.grounding import getting_started_skills
from surfaces.interactive_shell.runtime.startup.demo_repository import (
    demo_skill_for,
    repository_question,
)


def test_each_repository_demo_declares_the_question_the_shell_asks() -> None:
    # Arrange: the bundled demo skills, as the menu lists them.
    by_name = {skill.name: skill for skill in getting_started_skills()}

    # Act
    questions = {name: repository_question(skill) for name, skill in by_name.items()}

    # Assert: the two repository demos name their question; the others need none.
    assert questions["cicd-analytics-demo"] == "Which repository should I analyze?"
    assert questions["cicd-reliability-agent"] == "Which repository should the agent watch?"
    assert questions["slack-handoff"] is None
    assert questions["remote-managed-service"] is None


def test_the_demo_answer_maps_to_its_skill_by_the_exact_menu_row() -> None:
    # Arrange
    row = "Explore a repo and analyze its CI/CD performance (recommended)"

    # Act
    matched = demo_skill_for(row)
    unmatched = demo_skill_for("Explore a repo")

    # Assert
    assert matched is not None and matched.name == "cicd-analytics-demo"
    assert unmatched is None
