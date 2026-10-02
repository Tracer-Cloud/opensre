"""Serve the compact skill index for action prompts."""

from __future__ import annotations

from core.agent_harness.prompts.skills.snapshot.active_catalog import active_skill_catalog


def load_skills_index() -> str:
    """Return the active catalog's SKILLS INDEX for the stable system prompt."""
    return active_skill_catalog().current().index
