"""Agent-callable long-term memory tools: remember, forget, recall.

Thin wrappers over :mod:`core.domain.memory`; validation lives in
``validation.py`` and result shaping in ``results.py``.
"""

from __future__ import annotations

from typing import Any

from core.domain.memory import (
    MEMORY_SOURCES,
    MEMORY_TYPES,
    MEMORY_WRITE_POLICY,
    MemorySource,
    MemoryType,
    delete_memory,
    live_memories,
    load_live_memory,
    record_memory_usage,
    save_memory,
    search_memories_scored,
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

_REMEMBER_DESCRIPTION = (
    "Save durable knowledge to local long-term memory in the turn it comes up — do "
    "not wait for the user to say remember/save/note. Check the memory index (or "
    "memory_recall) first; pass an existing name to update that memory instead of "
    "creating a near-duplicate. Record where the fact came from with source and "
    "evidence.\n\n"
    f"{MEMORY_WRITE_POLICY}"
)


def _memory_available(sources: dict[str, dict[str, Any]]) -> bool:
    _ = sources
    from core.domain.memory import memory_available_here

    return memory_available_here()


def _verified(source: str | None, evidence: str) -> bool | None:
    """A user statement or a tool result with evidence verifies the fact; the assistant does not."""
    if source is None:
        return None
    if source == MemorySource.ASSISTANT:
        return False
    return source == MemorySource.USER or bool(evidence.strip())


@tool(
    name="memory_remember",
    display_name="Save knowledge",
    source="knowledge",
    description=_REMEMBER_DESCRIPTION,
    use_cases=[
        "The user mentions who they are or how they like to work (no special phrasing needed)",
        "A durable infrastructure fact surfaces (cluster names, naming conventions)",
        "A repository's identity, purpose, branch, or conventions should remain available after switching repos",
        "An investigation uncovers a lesson worth keeping (known-flaky service)",
        "A tool result shows a durable repository or CI fact, such as a flaky job and its fix",
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
            "source": {
                "type": "string",
                "enum": list(MEMORY_SOURCES),
                "description": (
                    "Where the fact came from: 'user' (the user said it), 'tool' (a "
                    "tool result in this session shows it; needs evidence), or "
                    "'assistant' (only the assistant concluded it)."
                ),
            },
            "evidence": {
                "type": "string",
                "description": (
                    "What shows the fact (max 200 chars): for 'tool', the tool and the "
                    "run, PR, commit or command; for 'user', a short quote."
                ),
            },
        },
        "required": ["name", "type", "description", "content"],
        "additionalProperties": False,
    },
)
def memory_remember(
    name: str,
    type: str,
    description: str,
    content: str,
    source: str | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    """Create or update one long-term memory file."""
    error = validate_remember_args(
        name, type, description, content, source=source, evidence=evidence
    )
    if error is not None:
        return error
    slug = normalize_name(name)
    assert slug is not None  # validate_remember_args already checked
    clean_evidence = evidence or ""
    # ``type`` and ``source`` were already validated by validate_remember_args.
    result = save_memory(
        slug=slug,
        memory_type=MemoryType(type),
        description=description,
        body=content,
        source=MemorySource(source) if source is not None else None,
        evidence=clean_evidence,
        verified=_verified(source, clean_evidence),
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


def _counted_as_used(result: dict[str, Any]) -> dict[str, Any]:
    """Count a use of each memory in ``result``; the size caps may leave ranked matches out."""
    record_memory_usage(memory["name"] for memory in result["memories"])
    return result


@tool(
    name="memory_recall",
    display_name="Recall",
    source="knowledge",
    description=(
        "Read long-term memories: pass 'name' for one full entry, 'query' to rank "
        "memories by relevance to any of its words (names, descriptions and bodies), "
        "or no arguments to list the index."
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
                "description": (
                    "Words to search for when the exact name is unknown; a memory "
                    "matching any of them is returned, best match first."
                ),
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
    """Read one memory, search memories, or list the index; demo output is never shown."""
    total = len(live_memories())
    if name:
        slug = normalize_name(name)
        record = load_live_memory(slug) if slug else None
        if record is None:
            return {"error": "not_found", "name": name, "total_stored": total}
        return _counted_as_used(recall_result([record], total_stored=total))
    if query:
        if not isinstance(query, str):
            return {"error": "invalid_query", "detail": "query must be a string"}
        scored = search_memories_scored(query, limit=normalize_recall_limit(limit))
        return _counted_as_used(
            recall_result(
                [record for record, _score in scored],
                total_stored=total,
                scores={record.slug: score for record, score in scored},
            )
        )
    return index_result(live_memories())


__all__ = ["memory_forget", "memory_recall", "memory_remember"]
