"""List and load a workflow card's on-demand Markdown references."""

from __future__ import annotations

from core.agent_harness.prompts.skills.content.reference_files import REFERENCE_NAME_RE
from core.agent_harness.prompts.skills.snapshot.active_catalog import active_skill_catalog


def skill_reference_names(name: str) -> tuple[str, ...]:
    """Return sorted reference slugs, which remain separate from automatic includes."""
    return active_skill_catalog().current().reference_names(name)


def load_skill_reference(name: str, reference: str) -> str:
    """Read a reference by plain slug, or return empty for an unknown skill or reference."""
    slug = reference.strip().lower()
    if not REFERENCE_NAME_RE.match(slug):
        return ""
    return active_skill_catalog().current().reference(name, slug)
