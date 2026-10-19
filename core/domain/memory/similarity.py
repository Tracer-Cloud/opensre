"""Near-duplicate detection between memories of the same type.

Two memories are compared by what they are *about*: the content words of
their slug and description, with filler that every preference shares ("user
prefers …", "wants …", "interested in …") removed. Bodies are left out on
purpose; a long body would dilute a match the header makes obvious.

The same measure backs three callers with different stakes:

- the audit reports pairs at :data:`POSSIBLE_DUPLICATE_SIMILARITY` and above
  as *possible* duplicates for a person to look at;
- extraction writes into an existing memory, and upkeep merges a cluster,
  only at :data:`DUPLICATE_SIMILARITY` and above.

Repository memories are never matched here: two different repositories read
alike ("primary repo for …") and are merged by ``owner/repo`` instead.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.domain.memory.models import WORKSPACE_ONLY_MEMORY_TYPES, MemoryRecord, MemoryType
from core.domain.memory.relevance import tokenize

#: Extraction and upkeep fold a memory into another at or above this.
DUPLICATE_SIMILARITY = 0.6
#: The audit reports a pair as a possible duplicate at or above this.
POSSIBLE_DUPLICATE_SIMILARITY = 0.4
#: Fewer subject words than this never count as a match: two-word headers
#: share a word by chance.
_MIN_SUBJECT_TOKENS = 2

_FILLER = frozenset(
    {
        "asked",
        "focus",
        "interest",
        "interested",
        "like",
        "memory",
        "note",
        "prefer",
        "preference",
        "preferred",
        "user",
        "value",
        "want",
    }
)

#: Types that are never folded together by subject words.
_NEVER_MATCHED: frozenset[MemoryType] = frozenset({MemoryType.REPOSITORY}) | (
    WORKSPACE_ONLY_MEMORY_TYPES
)


def subject_tokens(slug: str, description: str) -> frozenset[str]:
    """Content words of a memory's header, filler removed."""
    return frozenset(
        token
        for token in tokenize(f"{slug.replace('-', ' ')} {description}")
        if token not in _FILLER
    )


def _record_subject(record: MemoryRecord) -> frozenset[str]:
    return subject_tokens(record.slug, record.description)


def similarity(first: frozenset[str], second: frozenset[str]) -> float:
    """Jaccard overlap of two subject-word sets; ``0.0`` when either is too small."""
    if len(first) < _MIN_SUBJECT_TOKENS or len(second) < _MIN_SUBJECT_TOKENS:
        return 0.0
    return len(first & second) / len(first | second)


def matchable(memory_type: MemoryType) -> bool:
    """Whether memories of this type may be matched by subject words."""
    return memory_type not in _NEVER_MATCHED


def closest_duplicate(
    slug: str,
    memory_type: MemoryType,
    description: str,
    candidates: Sequence[MemoryRecord],
    *,
    threshold: float = DUPLICATE_SIMILARITY,
) -> MemoryRecord | None:
    """The live memory of the same type most like this header, if any reaches ``threshold``.

    A candidate with the same slug is the memory itself, not a duplicate of it.
    """
    if not matchable(memory_type):
        return None
    wanted = subject_tokens(slug, description)
    best: tuple[float, str, MemoryRecord] | None = None
    for record in candidates:
        if record.memory_type is not memory_type or record.slug == slug:
            continue
        score = similarity(wanted, _record_subject(record))
        if score < threshold:
            continue
        # Ties go to the most recently updated memory.
        key = (score, record.updated_at, record)
        if best is None or key[:2] > best[:2]:
            best = key
    return best[2] if best is not None else None


def duplicate_clusters(
    records: Sequence[MemoryRecord], *, threshold: float = DUPLICATE_SIMILARITY
) -> list[list[MemoryRecord]]:
    """Groups of two or more same-type memories linked by pairwise similarity.

    Each group is ordered most recently updated first, so its head is the
    memory to keep. Groups are ordered by their head's slug for stable output.
    """
    eligible = [record for record in records if matchable(record.memory_type)]
    subjects = [_record_subject(record) for record in eligible]
    parent = list(range(len(eligible)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left in range(len(eligible)):
        for right in range(left + 1, len(eligible)):
            if eligible[left].memory_type is not eligible[right].memory_type:
                continue
            if similarity(subjects[left], subjects[right]) >= threshold:
                parent[find(right)] = find(left)
    groups: dict[int, list[MemoryRecord]] = {}
    for index, record in enumerate(eligible):
        groups.setdefault(find(index), []).append(record)
    clusters = [group for group in groups.values() if len(group) > 1]
    for group in clusters:
        group.sort(key=lambda record: (record.updated_at, record.slug), reverse=True)
    clusters.sort(key=lambda group: group[0].slug)
    return clusters


__all__ = [
    "DUPLICATE_SIMILARITY",
    "POSSIBLE_DUPLICATE_SIMILARITY",
    "closest_duplicate",
    "duplicate_clusters",
    "matchable",
    "similarity",
    "subject_tokens",
]
