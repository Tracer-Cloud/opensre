"""Read-side views of the store: the prompt index, relevant memories, search, and recall.

All of them see only *live* memories — parsed files minus the fenced demo and
sample output (:mod:`core.domain.memory.fence`). Parsing, the relevance
statistics and the rendered index are cached per memory directory and file
signature, so a turn pays for a directory stat, not a re-parse.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from core.domain.memory.fence import is_fenced_record
from core.domain.memory.files import SUMMARY_FILENAME, memory_dir
from core.domain.memory.index import (
    DEFAULT_PROMPT_INDEX_CHARS,
    DEFAULT_RELEVANT_MEMORY_CHARS,
    DEFAULT_RELEVANT_MEMORY_ITEMS,
    render_relevant_entries,
)
from core.domain.memory.index import render_prompt_index as render_prompt_index_from_records
from core.domain.memory.models import MemoryRecord
from core.domain.memory.relevance import RelevanceIndex, query_terms, tokenize
from core.domain.memory.store import (
    PARSED_STORE_CACHE_SIZE,
    ensure_memory_store,
    load_memory,
    memory_dir_signature,
    parsed_memories,
)
from core.domain.memory.summaries import read_memory_summary
from core.domain.memory.usage import load_usage, record_memory_usage

type _Signature = tuple[tuple[str, int, int], ...]


@lru_cache(maxsize=PARSED_STORE_CACHE_SIZE)
def _live_store(directory_key: str, signature: _Signature) -> RelevanceIndex:
    """Relevance statistics over the live (unfenced) memories of one store state."""
    records = tuple(
        record
        for record in parsed_memories(directory_key, signature)
        if not is_fenced_record(record)
    )
    return RelevanceIndex(records)


def _current_store() -> tuple[Path, RelevanceIndex] | None:
    directory = memory_dir()
    if not directory.is_dir():
        return None
    return directory, _live_store(str(directory), memory_dir_signature(directory))


def live_memories() -> list[MemoryRecord]:
    """Memories that may reach the model, most recently updated first."""
    current = _current_store()
    return list(current[1].records) if current is not None else []


def load_live_memory(slug: str) -> MemoryRecord | None:
    """One memory by name as the model may see it; ``None`` when it is missing or fenced."""
    record = load_memory(slug)
    return None if record is None or is_fenced_record(record) else record


def search_memories_scored(query: str, *, limit: int = 5) -> list[tuple[MemoryRecord, float]]:
    """Live memories matching any query term, ranked by BM25, with their scores."""
    current = _current_store()
    if current is None:
        return []
    return current[1].rank(query_terms(query), limit=limit)


def search_memories(query: str, *, limit: int = 5) -> list[MemoryRecord]:
    """:func:`search_memories_scored` without the scores."""
    return [record for record, _score in search_memories_scored(query, limit=limit)]


def _file_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


@lru_cache(maxsize=PARSED_STORE_CACHE_SIZE)
def _cached_prompt_index(
    directory_key: str,
    signature: _Signature,
    _summary_signature: tuple[int, int] | None,
    max_chars: int,
) -> str:
    # Usage is read only when the memories or the summary change, so ordering by
    # usage cannot change the cached prompt prefix from one turn to the next.
    directory = Path(directory_key)
    return render_prompt_index_from_records(
        _live_store(directory_key, signature).records,
        usage=load_usage(directory),
        summary=read_memory_summary(directory),
        max_chars=max_chars,
    )


def render_prompt_index(*, max_chars: int = DEFAULT_PROMPT_INDEX_CHARS) -> str:
    """The stable memory block: ``memory_summary.md`` plus one line per live memory.

    ``""`` when there is nothing to show. Byte-identical across turns until a
    memory or the summary changes, so it can sit in the cached prompt prefix.
    Ensures the on-disk store exists so the first chat does not depend on a
    prior write to create ``~/.opensre/memory``.
    """
    directory = ensure_memory_store()
    return _cached_prompt_index(
        str(directory),
        memory_dir_signature(directory),
        _file_signature(directory / SUMMARY_FILENAME),
        max_chars,
    )


def select_relevant_memories(
    query_text: str,
    *,
    context: Sequence[str] = (),
    limit: int | None = None,
) -> list[MemoryRecord]:
    """Live memories relevant to a request, in prompt priority order.

    ``query_text`` is the request itself; ``context`` adds lower-weight terms
    (active repository and connected integration names). Matching ``user`` and
    ``preference`` memories come first. A request with no meaningful words
    selects nothing, whatever the context.
    """
    if not tokenize(query_text):
        return []
    current = _current_store()
    if current is None:
        return []
    ranked = current[1].rank(
        query_terms(query_text, context=context), limit=limit, personal_first=True
    )
    return [record for record, _score in ranked]


def render_relevant_memories(
    query_text: str,
    *,
    context: Sequence[str] = (),
    max_items: int = DEFAULT_RELEVANT_MEMORY_ITEMS,
    max_chars: int = DEFAULT_RELEVANT_MEMORY_CHARS,
) -> str:
    """Full text of the memories most relevant to this request; ``""`` when none match.

    Each body is capped and the whole block stays within ``max_chars``. Every
    memory shown is counted as used.
    """
    text, slugs = render_relevant_entries(
        select_relevant_memories(query_text, context=context),
        max_items=max_items,
        max_chars=max_chars,
    )
    record_memory_usage(slugs)
    return text


__all__ = [
    "live_memories",
    "load_live_memory",
    "render_prompt_index",
    "render_relevant_memories",
    "search_memories",
    "search_memories_scored",
    "select_relevant_memories",
]
