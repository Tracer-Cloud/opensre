"""Keep demo, sample and synthetic output out of long-term memory.

Onboarding demos create throwaway repositories (``<owner>/opensre-ci-repair-demo-<id>``)
and sample investigations whose results read like real facts. A memory whose
slug or description names such an artifact is *fenced*: it never reaches a
prompt, extraction does not save it, the remember tool refuses it, and
consolidation moves it to the archive.

The match is deliberately narrow. A bare ``demo``, ``sample`` or ``synthetic``
token counts only next to a word that makes it a demo artifact (``ci-repair-demo``,
``demo repository``, ``sample alert``), so real facts such as a sample rate or
synthetic monitors stay. ``user`` and ``preference`` memories are never fenced:
they record the person, and a preference about how demos should run is still a
preference.
"""

from __future__ import annotations

import re

from core.domain.memory.models import PERSONAL_MEMORY_TYPES, MemoryRecord, MemoryType

_DEMO_WORDS = frozenset({"demo", "demos"})
_SAMPLE_WORDS = frozenset({"sample", "samples", "synthetic"})
#: A word before ``demo`` that makes it a product demo (``ci-repair-demo``).
_DEMO_QUALIFIERS = frozenset({"ci", "repair", "fix", "onboarding", "opensre", "seeded"})
#: A word beside ``demo`` / ``sample`` / ``synthetic`` naming what was generated.
_ARTIFACT_WORDS = frozenset(
    {
        "alert",
        "alerts",
        "incident",
        "incidents",
        "investigation",
        "investigations",
        "pr",
        "prs",
        "rca",
        "repo",
        "repos",
        "repositories",
        "repository",
        "run",
        "runs",
        "scenario",
        "scenarios",
    }
)
_BENCHMARK_WORDS = frozenset({"cloudopsbench"})

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")
# ``owner/repo`` not preceded by a path or URL character, so ``/Users/x`` and
# ``github.com/a/b`` path segments are not read as repository names.
_REPOSITORY_RE = re.compile(r"(?<![\w./:-])[a-z0-9][a-z0-9-]*/([a-z0-9._-]+)")
_DEMO_PHRASE_RE = re.compile(
    r"\b(?:demo|sample|synthetic)s?[- ](?:"
    r"alerts?|incidents?|investigations?|prs?|pull requests?|rca|repos?|repositor(?:y|ies)|"
    r"runs?|scenarios?)\b"
    r"|\b(?:ci[- ]?repair|ci[- ]?fix|repair|onboarding|opensre(?: cloud)?)[- ]demos?\b"
    r"|\bsample:[a-z0-9_-]+"
)


def _tokens_name_demo(tokens: list[str]) -> bool:
    """True when a hyphenated name (slug or repository) names a demo artifact."""
    for index, token in enumerate(tokens):
        if token in _BENCHMARK_WORDS:
            return True
        before = tokens[index - 1] if index > 0 else ""
        after = tokens[index + 1] if index + 1 < len(tokens) else ""
        if token in _DEMO_WORDS and (before in _DEMO_QUALIFIERS or after in _ARTIFACT_WORDS):
            return True
        if token in _SAMPLE_WORDS and (before in _ARTIFACT_WORDS or after in _ARTIFACT_WORDS):
            return True
    return False


def _split(text: str) -> list[str]:
    return [token for token in _TOKEN_SPLIT_RE.split(text.lower()) if token]


def describes_demo_output(slug: str, description: str) -> bool:
    """True when ``slug`` or ``description`` names a demo, sample or synthetic artifact."""
    if _tokens_name_demo(_split(slug)):
        return True
    lowered = description.lower()
    if _DEMO_PHRASE_RE.search(lowered) or any(word in lowered for word in _BENCHMARK_WORDS):
        return True
    return any(_tokens_name_demo(_split(match)) for match in _REPOSITORY_RE.findall(lowered))


def is_fenced(slug: str, memory_type: MemoryType | str, description: str) -> bool:
    """True when a memory with these fields must stay out of prompts and the live store."""
    if MemoryType(memory_type) in PERSONAL_MEMORY_TYPES:
        return False
    return describes_demo_output(slug, description)


def is_fenced_record(record: MemoryRecord) -> bool:
    """:func:`is_fenced` for a stored record."""
    return is_fenced(record.slug, record.memory_type, record.description)


__all__ = ["describes_demo_output", "is_fenced", "is_fenced_record"]
