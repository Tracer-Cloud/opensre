"""Invalidate the active skill catalog."""

from __future__ import annotations

from core.agent_harness.prompts.skills.snapshot.active_catalog import active_skill_catalog


def clear_skills_caches() -> None:
    """Rebuild the catalog on next read (tests mutate on-disk skills)."""
    active_skill_catalog().invalidate()
