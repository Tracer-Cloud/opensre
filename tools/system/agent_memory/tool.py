"""Agent-callable long-term memory tools: remember, forget, recall.

Thin wrappers over :mod:`core.domain.memory`; validation lives in
``validation.py`` and result shaping in ``results.py``.
"""

from __future__ import annotations

from typing import Any

from core.domain.memory import (
    MEMORY_TYPES,
    MemoryType,
    delete_memory,
    list_memories,
    load_memory,
    save_memory,
    search_memories,
)
from core.domain.types.tools import ToolRole, ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from tools.system.agent_memory._evidence import map_memory_recall
from tools.system.agent_memory.results import (
    deleted_result,
    index_result,
    recall_result,
    saved_result,
)
from tools.system.agent_memory.validation import (
    normalize_name,
    normalize_recall_limit,
    validate_remember_args,
)


def _memory_available(sources: dict[str, dict[str, Any]]) -> bool:
    _ = sources
    from core.domain.memory import memory_available_here

    return memory_available_here()


@tool(
    name="memory_remember",
    display_name="Save knowledge",
    source="knowledge",
    description=(
        "Proactively save durable knowledge to local long-term memory whenever "
        "it would help future sessions — who the user is, infrastructure "
        "conventions, separate facts for each repository, preferences, and lessons "
        "from incidents. Do not wait for "
        "the user to say remember/save/note; if the fact is useful and stable, "
        "call this in the same turn. Check existing memories (or memory_recall) "
        "first; if an equivalent memory exists, pass its exact name to update it "
        "instead of creating a near-duplicate. Do not save secrets or credentials."
    ),
    use_cases=[
        "The user mentions who they are or how they like to work (no special phrasing needed)",
        "A durable infrastructure fact surfaces (cluster names, naming conventions)",
        "A repository's identity, purpose, branch, or conventions should remain available after switching repos",
        "An investigation uncovers a lesson worth keeping (known-flaky service)",
    ],
    tags=("safe", "fast", "no-credentials"),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    role=ToolRole.BOOKKEEPING,
    is_available=_memory_available,
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Stable kebab-case identifier, e.g. 'prod-cluster-conventions'. "
                    "Reusing an existing name updates that memory."
                ),
            },
            "type": {
                "type": "string",
                "enum": list(MEMORY_TYPES),
                "description": "Kind of knowledge this memory captures.",
            },
            "description": {
                "type": "string",
                "description": "One-line summary shown in the memory index (max 200 chars).",
            },
            "content": {"type": "string", "description": "Full markdown body of the memory."},
        },
        "required": ["name", "type", "description", "content"],
        "additionalProperties": False,
    },
)
def memory_remember(name: str, type: str, description: str, content: str) -> dict[str, Any]:
    """Create or update one long-term memory file."""
    error = validate_remember_args(name, type, description, content)
    if error is not None:
        return error
    slug = normalize_name(name)
    assert slug is not None  # validate_remember_args already checked
    # ``type`` was already validated against MEMORY_TYPES by validate_remember_args.
    result = save_memory(
        slug=slug, memory_type=MemoryType(type), description=description, body=content
    )
    if result is None:
        return {"error": "write_failed", "detail": "could not write the memory file to disk"}
    record, created = result
    return saved_result(record, created=created)


@tool(
    name="memory_forget",
    display_name="Forget",
    source="knowledge",
    description=(
        "Delete one memory from local long-term memory by exact name. Use when the "
        "user asks to forget something or a stored fact is no longer true."
    ),
    tags=("safe", "fast", "no-credentials"),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    is_available=_memory_available,
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Exact name of the memory to delete."}
        },
        "required": ["name"],
        "additionalProperties": False,
    },
)
def memory_forget(name: str) -> dict[str, Any]:
    """Delete one long-term memory file."""
    slug = normalize_name(name)
    if slug is None:
        return {"error": "invalid_name", "detail": "name does not normalize to a valid memory name"}
    return deleted_result(slug, deleted=delete_memory(slug))


@tool(
    name="memory_recall",
    display_name="Recall",
    source="knowledge",
    description=(
        "Read long-term memories: pass 'name' for one full entry, 'query' to keyword-"
        "search names, descriptions, and bodies, or no arguments to list the index."
    ),
    tags=("safe", "fast", "no-credentials"),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    is_available=_memory_available,
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Exact memory name to read in full."},
            "query": {
                "type": "string",
                "description": "Keyword search when the exact name is unknown.",
            },
            "limit": {"type": "integer", "default": 5},
        },
        "required": [],
        "additionalProperties": False,
    },
    evidence_mapper=map_memory_recall,
)
def memory_recall(
    name: str | None = None, query: str | None = None, limit: int = 5
) -> dict[str, Any]:
    """Read one memory, search memories, or list the index."""
    total = len(list_memories())
    if name:
        slug = normalize_name(name)
        record = load_memory(slug) if slug else None
        if record is None:
            return {"error": "not_found", "name": name, "total_stored": total}
        return recall_result([record], total_stored=total)
    if query:
        if not isinstance(query, str):
            return {"error": "invalid_query", "detail": "query must be a string"}
        return recall_result(
            search_memories(query, limit=normalize_recall_limit(limit)),
            total_stored=total,
        )
    return index_result(list_memories())


__all__ = ["memory_forget", "memory_recall", "memory_remember"]
