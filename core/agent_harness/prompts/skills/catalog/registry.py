"""Validate workflow catalogs and cache usable cards for runtime lookup."""

from __future__ import annotations

import logging
from collections import Counter
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from pydantic import ValidationError

from core.agent_harness.prompts.skills.catalog.contracts import (
    ActionSkill,
    SkillCatalog,
    SkillToolCall,
)
from core.agent_harness.prompts.skills.catalog.demo_menu import demo_skills, populate_demo_menu
from core.agent_harness.prompts.skills.catalog.discovery import iter_skill_paths
from core.agent_harness.prompts.skills.catalog.naming import normalize_skill_name
from core.agent_harness.prompts.skills.catalog.schema import (
    SkillCard,
    SkillCardError,
    parse_frontmatter,
)
from core.agent_harness.prompts.skills.content import files

logger = logging.getLogger(__name__)


def validate_skill_file(skill_path: Path) -> ActionSkill:
    """Validate one raw card, including the local files it includes."""
    raw = skill_path.read_text(encoding="utf-8")
    frontmatter, _body = parse_frontmatter(raw)
    card = SkillCard.model_validate(frontmatter)
    for ref in card.includes:
        if files.resolve_skill_include(skill_path, ref) is None:
            raise SkillCardError(f"includes: cannot resolve in-tree Markdown file {ref!r}")
    return ActionSkill(
        name=card.name,
        description=card.description,
        path=skill_path,
        recurring=card.recurring,
        getting_started=card.getting_started,
        demo_order=card.demo_order,
        pre_execute=tuple(
            SkillToolCall(call.tool, MappingProxyType(call.args)) for call in card.pre_execute
        ),
        includes=tuple(card.includes),
    )


def read_skill_catalog() -> SkillCatalog:
    """Validate every discovered card; retain diagnostics for CI and runtime reporting."""
    directory = files.skills_dir()
    if not directory.is_dir():
        return SkillCatalog((), ())
    skills: list[ActionSkill] = []
    diagnostics: list[str] = []
    for path in iter_skill_paths(directory):
        try:
            skills.append(validate_skill_file(path))
        except (OSError, UnicodeError, SkillCardError, ValidationError) as exc:
            diagnostics.append(f"{path}: {exc}")
    names = Counter(skill.name for skill in skills)
    labels = Counter(skill.getting_started for skill in skills if skill.getting_started)
    orders = Counter(skill.demo_order for skill in skills if skill.getting_started)
    valid: list[ActionSkill] = []
    for skill in skills:
        conflicts: list[str] = []
        if names[skill.name] > 1:
            conflicts.append(f"duplicate name {skill.name!r}")
        if skill.getting_started:
            if labels[skill.getting_started] > 1:
                conflicts.append("duplicate getting_started label")
            if orders[skill.demo_order] > 1:
                conflicts.append(f"duplicate demo_order {skill.demo_order}")
        if conflicts:
            diagnostics.append(f"{skill.path}: {', '.join(conflicts)}")
        else:
            valid.append(skill)
    populated = populate_demo_menu(tuple(valid))
    return SkillCatalog(populated.skills, (*diagnostics, *populated.diagnostics))


@lru_cache(maxsize=1)
def list_action_skills() -> tuple[ActionSkill, ...]:
    """Return valid skills; report and exclude broken cards without preventing startup."""
    catalog = read_skill_catalog()
    for diagnostic in catalog.diagnostics:
        logger.warning("Skipping invalid skill: %s", diagnostic)
    return catalog.skills


def getting_started_skills() -> tuple[ActionSkill, ...]:
    """Return the current selectable demos in menu order."""
    return demo_skills(list_action_skills())


def find_action_skill(name: str) -> ActionSkill | None:
    """Return the discovered skill for ``name``, or ``None`` if unknown."""
    needle = normalize_skill_name(name)
    if not needle:
        return None
    return next((skill for skill in list_action_skills() if skill.name == needle), None)
