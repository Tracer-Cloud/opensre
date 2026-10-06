"""Decide what provenance an extracted memory keeps, checked against the text it came from.

An extraction model labels each memory with a source, evidence and a verified
flag. Those labels are claims about what the model read, so they are checked
against it:

- ``user``: the evidence must quote the user's own words (an ellipsis may join
  quoted fragments).
- ``tool``: the evidence must appear in the tool output, or every identifier
  it names (a run or PR number, a commit, a job, a file, an ``owner/repo``)
  must; and the model must have marked the fact verified.

Either way the evidence must share a word with the memory, so a real but
unrelated quote cannot vouch for an invented fact. A claim that fails counts
as the assistant's word: a fact about infrastructure, repositories or
incidents is dropped, and a personal memory (``user`` or ``preference``) is
kept unverified with source ``assistant``. Only a claim that holds is stored
as verified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.domain.memory.models import (
    MEMORY_SOURCES,
    PERSONAL_MEMORY_TYPES,
    MemorySource,
    MemoryType,
)
from core.domain.memory.relevance import tokenize
from core.domain.memory.repository_ids import repository_ids

#: Typography a model may change when it quotes: curly quotes and Markdown emphasis.
_QUOTE_FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "`": None, "*": None})
_QUOTE_EDGES = "\"' "
_ELLIPSIS_RE = re.compile(r"…|\.{3}")
#: Punctuation around a token that is not part of the identifier it names.
_TOKEN_EDGES = "\"'`()[]{}<>,;:.!?#"
_JOINERS = frozenset("-_/.@")
_WORD_RUN_RE = re.compile(r"[a-z0-9]{3,}")


@dataclass(frozen=True)
class EvidenceCorpus:
    """What an extraction model read, by speaker: the user's messages and the tool lines."""

    user_messages: tuple[str, ...] = ()
    tool_lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class Provenance:
    """The source and verified flag stored with a memory."""

    source: MemorySource | None
    verified: bool | None


def _normalized(text: str) -> str:
    return " ".join(text.translate(_QUOTE_FOLD).lower().split())


def _quoted_in(evidence: str, texts: tuple[str, ...]) -> bool:
    """Whether each ellipsis-separated fragment of ``evidence`` appears in one of ``texts``."""
    fragments = [
        fragment
        for part in _ELLIPSIS_RE.split(evidence)
        if (fragment := _normalized(part).strip(_QUOTE_EDGES))
    ]
    haystacks = [_normalized(text) for text in texts]
    return bool(fragments) and all(
        any(fragment in haystack for haystack in haystacks) for fragment in fragments
    )


def _identifiers(evidence: str) -> set[str]:
    """Run, PR and commit numbers, job and file names, and ``owner/repo`` named in ``evidence``."""
    found = {f"{owner}/{name}".lower() for owner, name in repository_ids(evidence)}
    for raw in evidence.split():
        token = raw.strip(_TOKEN_EDGES).lower()
        if any(char.isdigit() for char in token) or (
            any(char in _JOINERS for char in token) and _WORD_RUN_RE.search(token)
        ):
            found.add(token)
    return found


def _shown_by_tools(evidence: str, tool_lines: tuple[str, ...]) -> bool:
    if _quoted_in(evidence, tool_lines):
        return True
    output = "\n".join(_normalized(line) for line in tool_lines)
    identifiers = _identifiers(evidence)
    return bool(identifiers) and all(identifier in output for identifier in identifiers)


def _supported(source: MemorySource, evidence: str, memory_text: str, read: EvidenceCorpus) -> bool:
    if not set(tokenize(evidence)) & set(tokenize(memory_text)):
        return False
    if source is MemorySource.USER:
        return _quoted_in(evidence, read.user_messages)
    return _shown_by_tools(evidence, read.tool_lines)


def checked_provenance(
    *,
    memory_type: MemoryType,
    source: str,
    evidence: str,
    verified: object,
    memory_text: str,
    read: EvidenceCorpus,
) -> Provenance | None:
    """The provenance to store for an extracted memory, or ``None`` when it must be dropped.

    ``source``, ``evidence`` and ``verified`` are the model's claims;
    ``memory_text`` is the memory's name, description and body, and ``read``
    is what the model was given.
    """
    claimed = MemorySource(source) if source in MEMORY_SOURCES else None
    if claimed is MemorySource.USER and _supported(claimed, evidence, memory_text, read):
        return Provenance(source=claimed, verified=verified is True)
    if (
        claimed is MemorySource.TOOL
        and verified is True
        and _supported(claimed, evidence, memory_text, read)
    ):
        return Provenance(source=claimed, verified=True)
    if memory_type not in PERSONAL_MEMORY_TYPES:
        return None
    if claimed is None:
        return Provenance(source=None, verified=None)
    return Provenance(source=MemorySource.ASSISTANT, verified=False)


__all__ = ["EvidenceCorpus", "Provenance", "checked_provenance"]
