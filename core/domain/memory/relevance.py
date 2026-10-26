"""Rank memories against a request with BM25 — no dependencies, no embeddings.

Each memory is one document built from its slug, description and body; the
slug and description count twice because they are written to summarize the
memory. Query terms carry weights so the user's own words outrank context
terms (active repository and connected integration names) that apply to every
request in a session.

This ranks memories for prompt inclusion and ``memory_recall``. It never
decides what a turn does.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from core.domain.memory.models import PERSONAL_MEMORY_TYPES, MemoryRecord
from core.domain.memory.repository_ids import repository_ids

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")
_MIN_TOKEN_CHARS = 3
_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "also",
        "and",
        "any",
        "are",
        "but",
        "can",
        "could",
        "did",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "her",
        "here",
        "him",
        "his",
        "how",
        "into",
        "its",
        "just",
        "let",
        "may",
        "might",
        "must",
        "not",
        "now",
        "off",
        "our",
        "ours",
        "out",
        "please",
        "she",
        "should",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "too",
        "via",
        "want",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "why",
        "will",
        "with",
        "would",
        "yes",
        "you",
        "your",
    }
)

#: BM25 term-frequency saturation and length normalization (the usual defaults).
_K1 = 1.2
_B = 0.75
#: Slug and description tokens count this many times in a memory's document.
_HEADER_WEIGHT = 2
#: Weight of the request's own words.
MESSAGE_TERM_WEIGHT = 1.0
#: Weight of context terms (active repositories, connected integrations).
CONTEXT_TERM_WEIGHT = 0.5


def _fold_plural(token: str) -> str:
    """Fold a trailing plural so ``alerts`` matches ``alert`` (queries and documents alike)."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Lowercase words with short tokens and stopwords dropped, plus repository identifiers.

    Splitting on every non-alphanumeric character breaks slugs, dotted names
    and ``owner/repo`` identifiers into their parts and drops the short ones,
    so each ``owner/repo`` is also kept whole, along with an owner or name
    shorter than a word token: otherwise ``a/b`` could never be matched.
    """
    lowered = text.lower()
    tokens = [
        _fold_plural(token)
        for token in _TOKEN_SPLIT_RE.split(lowered)
        if len(token) >= _MIN_TOKEN_CHARS and token not in _STOPWORDS
    ]
    for owner, name in repository_ids(lowered):
        tokens.append(f"{owner}/{name}")
        tokens.extend(part for part in (owner, name) if len(part) < _MIN_TOKEN_CHARS)
    return tokens


def query_terms(text: str, *, context: Iterable[str] = ()) -> dict[str, float]:
    """Weighted query terms: the request's words at full weight, context terms at half.

    A term in both keeps the higher weight; repeating a word does not add weight.
    """
    terms: dict[str, float] = {}
    for token in tokenize(" ".join(context)):
        terms[token] = CONTEXT_TERM_WEIGHT
    for token in tokenize(text):
        terms[token] = MESSAGE_TERM_WEIGHT
    return terms


@dataclass(frozen=True)
class _Document:
    record: MemoryRecord
    frequencies: Mapping[str, int]
    length: int


class RelevanceIndex:
    """BM25 statistics over a fixed set of memories; build once per store state."""

    def __init__(self, records: Sequence[MemoryRecord]) -> None:
        documents: list[_Document] = []
        document_frequency: Counter[str] = Counter()
        for record in records:
            header = tokenize(f"{record.slug} {record.description}")
            tokens = header * _HEADER_WEIGHT + tokenize(record.body)
            frequencies = Counter(tokens)
            documents.append(_Document(record, frequencies, len(tokens)))
            document_frequency.update(frequencies.keys())
        self._documents = tuple(documents)
        self._records = tuple(records)
        total = len(documents)
        self._average_length = (
            sum(document.length for document in documents) / total if total else 0.0
        )
        self._idf = {
            term: math.log(1.0 + (total - count + 0.5) / (count + 0.5))
            for term, count in document_frequency.items()
        }

    @property
    def records(self) -> tuple[MemoryRecord, ...]:
        """The indexed memories, in the order they were given."""
        return self._records

    def _score(self, document: _Document, terms: Mapping[str, float]) -> float:
        if not document.length:
            return 0.0
        norm = _K1 * (1.0 - _B + _B * document.length / (self._average_length or 1.0))
        score = 0.0
        for term, weight in terms.items():
            frequency = document.frequencies.get(term, 0)
            if frequency:
                score += weight * self._idf[term] * frequency * (_K1 + 1.0) / (frequency + norm)
        return score

    def rank(
        self,
        terms: Mapping[str, float],
        *,
        limit: int | None = None,
        personal_first: bool = False,
    ) -> list[tuple[MemoryRecord, float]]:
        """Memories with a nonzero score, best first; ties go to the most recently updated.

        ``personal_first`` puts matching ``user`` / ``preference`` memories ahead
        of the rest, each group still ordered by score.
        """
        if not terms:
            return []
        scored = [
            (document.record, score)
            for document in self._documents
            if (score := self._score(document, terms)) > 0.0
        ]
        # Stable sorts: recency first, then score, then (optionally) the personal group.
        scored.sort(key=lambda item: item[0].updated_at, reverse=True)
        scored.sort(key=lambda item: round(item[1], 9), reverse=True)
        if personal_first:
            scored.sort(key=lambda item: item[0].memory_type not in PERSONAL_MEMORY_TYPES)
        return scored if limit is None else scored[: max(limit, 0)]


__all__ = [
    "CONTEXT_TERM_WEIGHT",
    "MESSAGE_TERM_WEIGHT",
    "RelevanceIndex",
    "query_terms",
    "tokenize",
]
