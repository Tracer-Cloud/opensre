"""Long-term memory store: local, file-based semantic memory for the agent.

One markdown file per memory under ``~/.opensre/memory/`` (with an environment
override), plus a generated ``MEMORY.md`` index. The prompt carries a stable
index of every memory and, per turn, the full text of the memories relevant to
the request. See ``docs/platform/memory.mdx`` for the user-facing behavior.
"""

from __future__ import annotations

from core.domain.memory.consolidation import (
    ConsolidationInput,
    ConsolidationResult,
    consolidate_memories,
)
from core.domain.memory.fence import is_fenced
from core.domain.memory.index import (
    DEFAULT_PROMPT_INDEX_CHARS,
    DEFAULT_RELEVANT_MEMORY_CHARS,
    DEFAULT_RELEVANT_MEMORY_ITEMS,
)
from core.domain.memory.models import (
    MAX_BODY_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_EVIDENCE_CHARS,
    MEMORY_SOURCES,
    MEMORY_TYPES,
    MemoryRecord,
    MemorySource,
    MemoryType,
)
from core.domain.memory.policy import MEMORY_WRITE_POLICY
from core.domain.memory.provenance import EvidenceCorpus, Provenance, checked_provenance
from core.domain.memory.recall import (
    live_memories,
    load_live_memory,
    render_prompt_index,
    render_relevant_memories,
    search_memories,
    search_memories_scored,
    select_relevant_memories,
)
from core.domain.memory.safety import (
    MemorySafetyIssue,
    find_memory_safety_issues,
    redact_memory_unsafe_text,
)
from core.domain.memory.settings import (
    auto_extract_enabled,
    gateway_memory_enabled,
    memory_available_here,
    memory_enabled,
)
from core.domain.memory.slugs import is_valid_slug, slugify
from core.domain.memory.store import (
    delete_memory,
    ensure_memory_store,
    list_memories,
    load_memory,
    memory_dir,
    memory_path,
    rebuild_index,
    save_memory,
)
from core.domain.memory.summaries import SessionSummary, append_session_summary
from core.domain.memory.usage import record_memory_usage

__all__ = [
    "DEFAULT_PROMPT_INDEX_CHARS",
    "DEFAULT_RELEVANT_MEMORY_CHARS",
    "DEFAULT_RELEVANT_MEMORY_ITEMS",
    "MAX_BODY_CHARS",
    "MAX_DESCRIPTION_CHARS",
    "MAX_EVIDENCE_CHARS",
    "MEMORY_SOURCES",
    "MEMORY_TYPES",
    "MEMORY_WRITE_POLICY",
    "ConsolidationInput",
    "ConsolidationResult",
    "EvidenceCorpus",
    "MemoryRecord",
    "MemorySafetyIssue",
    "MemorySource",
    "MemoryType",
    "Provenance",
    "SessionSummary",
    "append_session_summary",
    "auto_extract_enabled",
    "checked_provenance",
    "consolidate_memories",
    "delete_memory",
    "ensure_memory_store",
    "find_memory_safety_issues",
    "gateway_memory_enabled",
    "is_fenced",
    "is_valid_slug",
    "list_memories",
    "live_memories",
    "load_live_memory",
    "load_memory",
    "memory_available_here",
    "memory_dir",
    "memory_enabled",
    "memory_path",
    "rebuild_index",
    "record_memory_usage",
    "redact_memory_unsafe_text",
    "render_prompt_index",
    "render_relevant_memories",
    "save_memory",
    "search_memories",
    "search_memories_scored",
    "select_relevant_memories",
    "slugify",
]
