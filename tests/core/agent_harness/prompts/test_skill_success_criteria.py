"""Every skill card has verifiable success criteria the host can show."""

from __future__ import annotations

import core.agent_harness.prompts.skills as skills
from core.agent_harness.prompts.skills.catalog.schema import parse_frontmatter
from core.agent_harness.prompts.skills.catalog.success_criteria import (
    SUCCESS_CRITERIA,
    success_section,
)
from tools.registry_skill_guidance import _skill_guidance_files


def _card_names() -> set[str]:
    names = {skill.name for skill in skills.list_action_skills()}
    for path in _skill_guidance_files():
        frontmatter, _body = parse_frontmatter(path.read_text(encoding="utf-8"))
        names.add(str(frontmatter["name"]))
    return names


def test_every_skill_has_verifiable_success_criteria() -> None:
    assert set(SUCCESS_CRITERIA) == _card_names()
    for items in SUCCESS_CRITERIA.values():
        assert items
        assert all("`" in item for item in items)


def test_loaded_workflow_skill_shows_success_criteria() -> None:
    body = skills.load_skill_body("repair-github-ci")
    assert success_section("repair-github-ci") in body
