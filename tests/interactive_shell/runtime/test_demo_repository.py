"""The repository question a demo needs is read from the skill that declares it."""

from __future__ import annotations

from pathlib import Path

from core.agent_harness.prompts.skills.loader import (
    ActionSkill,
    SkillAfterToolHook,
    SkillToolCall,
)
from core.agent_harness.spi.grounding import getting_started_skills
from surfaces.interactive_shell.runtime.startup.demo_repository import (
    demo_skill_for,
    repository_question,
)

_QUESTION = "Which repository should the agent watch?"


def _skill_declaring(hook: SkillAfterToolHook) -> ActionSkill:
    return ActionSkill(
        name="declaring-a-repository-menu",
        description="",
        path=Path("SKILL.md"),
        after_tool=(hook,),
    )


def test_bundled_demos_leave_the_repository_question_to_the_model() -> None:
    # Arrange: the bundled demo skills, as the menu lists them.
    by_name = {skill.name: skill for skill in getting_started_skills()}

    # Act
    questions = {name: repository_question(skill) for name, skill in by_name.items()}

    # Assert: both repository demos ask with their own ask_user_choice step,
    # so the shell asks nothing ahead of the model; the others need no repository.
    assert questions == {
        "analyzing-github-ci-performance": None,
        "scheduling-github-ci-fixes": None,
        "connecting-slack": None,
    }


def test_a_declared_scan_menu_is_the_question_the_shell_asks() -> None:
    # Arrange: a skill that hooks its repository menu onto the workspace scan.
    declared = _skill_declaring(
        SkillAfterToolHook(
            after="scan_local_git_workspace",
            call=SkillToolCall(tool="ask_user_choice", args={"title": _QUESTION}),
            options_from="local_git_scan_repos",
        )
    )
    # A menu fed from anything but the scan result is not a repository question.
    other_source = _skill_declaring(
        SkillAfterToolHook(
            after="scan_local_git_workspace",
            call=SkillToolCall(tool="ask_user_choice", args={"title": _QUESTION}),
            options_from="something_else",
        )
    )

    # Act / Assert
    assert repository_question(declared) == _QUESTION
    assert repository_question(other_source) is None


def test_the_demo_answer_maps_to_its_skill_by_the_exact_menu_row() -> None:
    # Arrange
    row = "Explore a repo and analyze its CI/CD performance (recommended)"

    # Act
    matched = demo_skill_for(row)
    unmatched = demo_skill_for("Explore a repo")

    # Assert
    assert matched is not None and matched.name == "analyzing-github-ci-performance"
    assert unmatched is None
