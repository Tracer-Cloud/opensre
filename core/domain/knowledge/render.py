"""Workspace knowledge as the model sees it.

Two views, mirroring personal memory:

- the **workspace block**, in the cached prompt half: the organization entry
  in full (who the company is and who is on the team helps nearly every
  turn), then one line per other entry. Byte-identical until a scan rewrites
  an entry, so it never breaks the prompt cache between turns;
- **relevant entries**, rebuilt per turn: the full text of the integration
  and learned entries that match the request. The organization entry is
  never repeated there.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from core.domain.knowledge.settings import knowledge_available_here
from core.domain.knowledge.store import ORGANIZATION_SLUG, workspace_store
from core.domain.memory import MemoryRecord, MemoryType, select_relevant_memories
from core.domain.memory.index import render_index_lines, render_relevant_entries
from core.domain.memory.recall import live_memories
from core.domain.memory.store import memory_dir_signature

#: Budget for the organization entry inlined in the cached block.
_ORGANIZATION_INLINE_CHARS = 2_000
#: Budget for the index lines of the other entries.
_WORKSPACE_INDEX_CHARS = 1_500
_RELEVANT_ITEMS = 3
_RELEVANT_CHARS = 3_000
_ELLIPSIS = "\n…[truncated; memory_recall name=organization for the rest]"


def _clip_body(body: str, limit: int) -> str:
    text = body.strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - len(_ELLIPSIS), 0)].rstrip() + _ELLIPSIS


def render_workspace_view(records: Sequence[MemoryRecord]) -> str:
    """The cached block's text for ``records``; ``""`` when there is nothing to show."""
    organization = next((r for r in records if r.slug == ORGANIZATION_SLUG), None)
    others = [r for r in records if r.slug != ORGANIZATION_SLUG]
    others.sort(key=lambda r: (r.memory_type is not MemoryType.INTEGRATION, r.slug))
    parts: list[str] = []
    if organization is not None:
        parts.append(
            f"Organization (updated {organization.updated_at[:10]}):\n"
            + _clip_body(organization.body, _ORGANIZATION_INLINE_CHARS)
        )
    lines = render_index_lines(others, max_chars=_WORKSPACE_INDEX_CHARS)
    if lines:
        parts.append("Entries:\n" + lines)
    return "\n\n".join(parts)


@lru_cache(maxsize=8)
def _cached_view(_directory_key: str, _signature: tuple[tuple[str, int, int], ...]) -> str:
    # Keyed by directory and file signature; the bound store is read inside.
    return render_workspace_view(live_memories())


def render_workspace_block() -> str:
    """The workspace view for the cached prompt half; ``""`` with no entries or no access."""
    if not knowledge_available_here():
        return ""
    with workspace_store() as bound:
        return _cached_view(str(bound), memory_dir_signature(Path(bound)))


def render_relevant_workspace_entries(query_text: str, *, context: Sequence[str] = ()) -> str:
    """Full text of the workspace entries that match this request; ``""`` when none do.

    Workspace entries are not counted as used: nothing orders or ages them by
    use, and counting would make every matching turn rewrite a file shared by
    the whole organization.
    """
    if not knowledge_available_here():
        return ""
    with workspace_store():
        ranked = [
            record
            for record in select_relevant_memories(query_text, context=context)
            if record.slug != ORGANIZATION_SLUG
        ]
    text, _slugs = render_relevant_entries(
        ranked, max_items=_RELEVANT_ITEMS, max_chars=_RELEVANT_CHARS
    )
    return text


__all__ = [
    "render_relevant_workspace_entries",
    "render_workspace_block",
    "render_workspace_view",
]
