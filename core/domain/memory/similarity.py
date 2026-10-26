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

Word overlap alone would fold different facts that read alike, so three
guards come first and a pair that trips any of them never matches:

- **identifiers** — every token carrying a digit (``eks-prod-1``,
  ``us-east-2``, ``gpt-5.4``, ``INC-42``) must be the same on both sides;
- **repositories** — named ``owner/repo`` identifiers must agree when both
  name one;
- **negation** — "never", "not", "don't" and the like must be on both sides
  or neither ("prefers PRs" is not "never use PRs").
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

from core.domain.memory.models import WORKSPACE_ONLY_MEMORY_TYPES, MemoryRecord, MemoryType
from core.domain.memory.relevance import tokenize
from core.domain.memory.repository_ids import repository_ids

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


#: Compound tokens (``eks-prod-1``, ``gpt-5.4-mini``) kept whole so their digits stay attached.
_COMPOUND_RE = re.compile(r"[a-z0-9]+(?:[-_.:][a-z0-9]+)*")
_NEGATION_RE = re.compile(r"\b(?:not|never|no|none|don'?t|doesn'?t|avoid|without|stop)\b")


@dataclass(frozen=True)
class Subject:
    """What a memory's header is about, as compared for duplicates."""

    words: frozenset[str]
    identifiers: frozenset[str]
    repositories: frozenset[str]
    negated: bool


@lru_cache(maxsize=4096)
def subject_of(slug: str, description: str) -> Subject:
    """The subject of a memory header: content words plus the guards' facts."""
    header = f"{slug} {description}".lower()
    readable = f"{slug.replace('-', ' ')} {description}"
    return Subject(
        words=frozenset(token for token in tokenize(readable) if token not in _FILLER),
        identifiers=frozenset(
            token
            for token in (*_COMPOUND_RE.findall(description.lower()), *slug.split("-"))
            if any(char.isdigit() for char in token)
        ),
        repositories=frozenset(f"{owner}/{name}" for owner, name in repository_ids(header)),
        negated=bool(_NEGATION_RE.search(header.replace("-", " "))),
    )


def _record_subject(record: MemoryRecord) -> Subject:
    return subject_of(record.slug, record.description)


def similarity(first: Subject, second: Subject) -> float:
    """Jaccard overlap of two subjects' words; ``0.0`` when a guard trips or either is too small."""
    if first.negated != second.negated or first.identifiers != second.identifiers:
        return 0.0
    if first.repositories and second.repositories and first.repositories != second.repositories:
        return 0.0
    if len(first.words) < _MIN_SUBJECT_TOKENS or len(second.words) < _MIN_SUBJECT_TOKENS:
        return 0.0
    return len(first.words & second.words) / len(first.words | second.words)


def matchable(memory_type: MemoryType) -> bool:
    """Whether memories of this type may be matched by subject words."""
    return memory_type not in _NEVER_MATCHED


def closest_duplicate(
    slug: str,
    memory_type: MemoryType,
    description: str,
    candidates: Sequence[MemoryRecord],
) -> MemoryRecord | None:
    """The live memory of the same type most like this header, if any is a duplicate.

    A candidate with the same slug is the memory itself, not a duplicate of it.
    """
    if not matchable(memory_type):
        return None
    wanted = subject_of(slug, description)
    best: tuple[float, str, MemoryRecord] | None = None
    for record in candidates:
        if record.memory_type is not memory_type or record.slug == slug:
            continue
        score = similarity(wanted, _record_subject(record))
        if score < DUPLICATE_SIMILARITY:
            continue
        # Ties go to the most recently updated memory.
        if best is None or (score, record.updated_at) > best[:2]:
            best = (score, record.updated_at, record)
    return best[2] if best is not None else None


def duplicate_clusters(
    records: Sequence[MemoryRecord], *, threshold: float = DUPLICATE_SIMILARITY
) -> list[list[MemoryRecord]]:
    """Groups of two or more same-type memories, each similar to the group's head.

    The most recently updated memory heads a group, and only memories similar
    to that head join it, never memories reached through a chain (A like B,
    B like C does not put C with A). Each group is ordered most recently
    updated first, so its head is the memory to keep; groups are ordered by
    their head's slug for stable output.
    """
    # Only memories that agree on type and on every guard can score above zero,
    # so compare within those buckets instead of across the whole store.
    buckets: dict[tuple[MemoryType, bool, frozenset[str]], list[MemoryRecord]] = {}
    for record in sorted(
        (record for record in records if matchable(record.memory_type)),
        key=lambda record: (record.updated_at, record.slug),
        reverse=True,
    ):
        subject = _record_subject(record)
        buckets.setdefault((record.memory_type, subject.negated, subject.identifiers), []).append(
            record
        )
    clusters: list[list[MemoryRecord]] = []
    for bucket in buckets.values():
        grouped: set[str] = set()
        for index, head in enumerate(bucket):
            if head.slug in grouped:
                continue
            head_subject = _record_subject(head)
            group = [head] + [
                other
                for other in bucket[index + 1 :]
                if other.slug not in grouped
                and similarity(head_subject, _record_subject(other)) >= threshold
            ]
            if len(group) > 1:
                grouped.update(record.slug for record in group)
                clusters.append(group)
    clusters.sort(key=lambda group: group[0].slug)
    return clusters


__all__ = [
    "DUPLICATE_SIMILARITY",
    "POSSIBLE_DUPLICATE_SIMILARITY",
    "closest_duplicate",
    "duplicate_clusters",
    "similarity",
    "subject_of",
]
