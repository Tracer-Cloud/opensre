"""Serve the compact skill index for action prompts."""

from __future__ import annotations

from core.agent_harness.prompts.skills.content.index_render import render_skills_prompt_index
from core.agent_harness.prompts.skills.snapshot.active_catalog import active_skill_catalog


def load_skills_index() -> str:
    """Return the active catalog's SKILLS INDEX for the stable system prompt."""
    return active_skill_catalog().current().index


def load_skills_prompt_index() -> str:
    """Return workflow names and discovery instructions for the model prompt."""
    return render_skills_prompt_index(active_skill_catalog().current().skills)
