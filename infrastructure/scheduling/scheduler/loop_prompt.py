"""What a manual loop runs and shows: its shipped template's text, else its stored copy.

An agent loop bound to a skill also runs that workflow card, read at each tick
from the active catalog or, for a skill installed as a folder, from its SKILL.md.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from core.agent_harness import LoopTemplate, load_loop_template
from core.agent_harness.spi.skill_releases import active_skill_catalog
from infrastructure.scheduling.scheduler.installed_skill import (
    installed_skill_folder,
    is_installed_skill_path,
    read_installed_skill,
)
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_DESCRIPTION_PARAM,
    LOOP_PROMPT_PARAM,
    LOOP_TEMPLATE_PARAM,
)

logger = logging.getLogger(__name__)


def _shipped_template(params: Mapping[str, str]) -> LoopTemplate | None:
    template = str(params.get(LOOP_TEMPLATE_PARAM, "")).strip()
    if not template:
        return None
    try:
        return load_loop_template(template)
    except KeyError:
        logger.warning("Loop template %r is not shipped; using the stored copy.", template)
        return None


def current_loop_prompt(params: Mapping[str, str]) -> str:
    """Return the text a tick runs; a template no longer shipped falls back to the stored copy."""
    shipped = _shipped_template(params)
    return shipped.prompt if shipped else str(params.get(LOOP_PROMPT_PARAM, "")).strip()


def current_loop_description(params: Mapping[str, str]) -> str:
    """Return the operator's description, else the shipped template's."""
    stored = str(params.get(LOOP_DESCRIPTION_PARAM, "")).strip()
    if stored:
        return stored
    shipped = _shipped_template(params)
    return shipped.description if shipped else ""


def loop_skill_reference(skill: str) -> str:
    """What a loop stores to follow ``skill``: an installed folder's path, else the card's name.

    Raises ``RuntimeError`` as :func:`loop_skill_recipe` does.
    """
    if is_installed_skill_path(skill):
        read_installed_skill(skill)
        return str(installed_skill_folder(skill))
    return loop_skill_recipe(skill)[0]


def loop_skill_recipe(skill: str) -> tuple[str, str]:
    """Return the heading and body of the skill a loop follows.

    ``skill`` names a card in the active catalog, whose canonical name heads its
    rendered body, or is the path of an installed skill folder, headed by its
    name and folder so the tick can find files it mentions. Raises
    ``RuntimeError`` when the skill is missing, or when a card needs what a
    scheduled tick cannot give it (helper scripts, an entry menu), so a tick
    fails instead of running without its instructions.
    """
    if is_installed_skill_path(skill):
        name, body = read_installed_skill(skill)
        return f"{name}, installed at {installed_skill_folder(skill)}", body
    snapshot = active_skill_catalog().current()
    found = snapshot.find(skill)
    if found is None:
        raise RuntimeError(f"Loop skill {skill.strip()!r} is not installed.")
    if found.script_tools or found.entry_menu is not None:
        raise RuntimeError(
            f"Loop skill {found.name!r} needs helper scripts or a menu, "
            "which a scheduled agent loop cannot run."
        )
    return found.name, snapshot.body(found.name)


__all__ = [
    "current_loop_description",
    "current_loop_prompt",
    "loop_skill_recipe",
    "loop_skill_reference",
]
