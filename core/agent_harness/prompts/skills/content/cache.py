"""Invalidate the cached skill catalog and prompt index together."""

from __future__ import annotations

from core.agent_harness.prompts.skills.catalog.registry import list_action_skills
from core.agent_harness.prompts.skills.content.index import load_skills_index


def clear_skills_caches() -> None:
    """Drop cached discovery/index (tests mutate on-disk skills)."""
    list_action_skills.cache_clear()
    load_skills_index.cache_clear()
