"""Validate the workflow cards under one catalog root."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from core.agent_harness.prompts.skills.catalog.contracts import ActionSkill, SkillCatalog
from core.agent_harness.prompts.skills.catalog.demo_menu import populate_demo_menu
from core.agent_harness.prompts.skills.catalog.discovery import iter_skill_paths
from core.agent_harness.prompts.skills.catalog.schema import (
    SkillCard,
    SkillCardError,
    parse_frontmatter,
)
from core.agent_harness.prompts.skills.catalog.script_tools import load_script_tools
from core.agent_harness.prompts.skills.content import files


def validate_skill_file(
    skill_path: Path, *, root: Path | None = None, strict: bool = False
) -> ActionSkill:
    """Validate one raw card, including the local files it includes.

    ``root`` is the catalog root includes must stay inside (the bundled tree by
    default). ``strict`` is the CI contract: no ``last_changed_at`` after today.
    """
    raw = skill_path.read_text(encoding="utf-8")
    frontmatter, _body = parse_frontmatter(raw)
    card = SkillCard.model_validate(frontmatter, context={"strict_dates": strict})
    try:
        script_tools = load_script_tools(skill_path, card.script_tools)
    except ValueError as exc:
        raise SkillCardError(str(exc)) from exc
    for ref in card.includes:
        if files.resolve_skill_include(skill_path, ref, root=root) is None:
            raise SkillCardError(f"includes: cannot resolve in-tree Markdown file {ref!r}")
    return ActionSkill(
        name=card.name,
        description=card.description,
        path=skill_path,
        recurring=card.recurring,
        getting_started=card.getting_started,
        demo_order=card.demo_order,
        includes=tuple(card.includes),
        script_tools=script_tools,
        version=card.metadata.version,
    )


def read_skill_catalog(directory: Path | None = None, *, strict: bool = False) -> SkillCatalog:
    """Validate every discovered card; retain diagnostics for CI and runtime reporting.

    ``directory`` defaults to the bundled tree, read at call time so tests that
    replace ``files.skills_dir`` keep working.
    """
    root = directory if directory is not None else files.skills_dir()
    if not root.is_dir():
        return SkillCatalog((), ())
    skills: list[ActionSkill] = []
    diagnostics: list[str] = []
    for path in iter_skill_paths(root):
        try:
            skills.append(validate_skill_file(path, root=root, strict=strict))
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


__all__ = ["read_skill_catalog", "validate_skill_file"]
