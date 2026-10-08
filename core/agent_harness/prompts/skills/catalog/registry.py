"""Look up validated workflow cards in the active catalog."""

from __future__ import annotations

from core.agent_harness.prompts.skills.catalog.contracts import ActionSkill
from core.agent_harness.prompts.skills.snapshot.active_catalog import active_skill_catalog


def list_action_skills() -> tuple[ActionSkill, ...]:
    """Return valid skills; broken cards are reported and excluded without preventing startup."""
    return active_skill_catalog().current().skills


def getting_started_skills() -> tuple[ActionSkill, ...]:
    """Return the current selectable demos in menu order."""
    return active_skill_catalog().current().getting_started()


def find_action_skill(name: str) -> ActionSkill | None:
    """Return the discovered skill for ``name``, or ``None`` if unknown."""
    return active_skill_catalog().current().find(name)
