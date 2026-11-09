"""Load a skill's rendered instructions from the active catalog."""

from __future__ import annotations

from core.agent_harness.prompts.skills.snapshot.active_catalog import active_skill_catalog


def load_skill_body(name: str) -> str:
    """Return instructions with includes, report template and host sections, or empty if unknown."""
    return active_skill_catalog().current().body(name)
