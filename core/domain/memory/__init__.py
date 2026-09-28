"""Long-term memory store: local, file-based semantic memory for the agent.

One markdown file per memory under ``~/.opensre/memory/`` (with an environment
override), plus a generated ``MEMORY.md`` index. See
``docs/platform/memory.mdx`` for the user-facing behavior.
"""

from __future__ import annotations

from core.domain.memory.index import (
    DEFAULT_PROMPT_INDEX_CHARS,
)
from core.domain.memory.models import (
    MAX_BODY_CHARS,
    MAX_DESCRIPTION_CHARS,
    MEMORY_TYPES,
    MemoryRecord,
    MemoryType,
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
    render_prompt_index,
    save_memory,
    search_memories,
)

__all__ = [
    "DEFAULT_PROMPT_INDEX_CHARS",
    "MAX_BODY_CHARS",
    "MAX_DESCRIPTION_CHARS",
    "MEMORY_TYPES",
    "MemorySafetyIssue",
    "MemoryRecord",
    "MemoryType",
    "auto_extract_enabled",
    "delete_memory",
    "ensure_memory_store",
    "gateway_memory_enabled",
    "is_valid_slug",
    "list_memories",
    "load_memory",
    "memory_available_here",
    "memory_dir",
    "memory_enabled",
    "memory_path",
    "rebuild_index",
    "redact_memory_unsafe_text",
    "render_prompt_index",
    "save_memory",
    "search_memories",
    "find_memory_safety_issues",
    "slugify",
]
