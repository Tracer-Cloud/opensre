"""Argument validation for the agent memory tools."""

from __future__ import annotations

from typing import Any

from core.domain.memory import (
    MAX_EVIDENCE_CHARS,
    MEMORY_SOURCES,
    MEMORY_TYPES,
    MemorySource,
    find_memory_safety_issues,
    is_fenced,
    is_valid_slug,
    slugify,
)

DEFAULT_RECALL_LIMIT = 5
MAX_RECALL_LIMIT = 20


def normalize_name(name: Any) -> str | None:
    """Slugify a tool-supplied memory name; ``None`` when nothing usable remains."""
    if not isinstance(name, str):
        return None
    slug = slugify(name)
    return slug if is_valid_slug(slug) else None


def _validate_provenance(source: Any, evidence: Any) -> dict[str, Any] | None:
    if source is not None and source not in MEMORY_SOURCES:
        return {
            "error": "invalid_source",
            "detail": f"source must be one of {', '.join(MEMORY_SOURCES)}",
        }
    if evidence is not None and not isinstance(evidence, str):
        return {"error": "invalid_evidence", "detail": "evidence must be a string"}
    if source == MemorySource.TOOL and not (evidence or "").strip():
        return {
            "error": "missing_evidence",
            "detail": (
                "a fact taken from tool output needs evidence: the tool and the run, "
                "PR, commit or command that shows it"
            ),
        }
    if evidence is not None and len(evidence) > MAX_EVIDENCE_CHARS:
        return {
            "error": "evidence_too_long",
            "detail": f"evidence must be at most {MAX_EVIDENCE_CHARS} characters",
        }
    return None


def validate_remember_args(
    name: Any,
    memory_type: Any,
    description: Any,
    content: Any,
    *,
    source: Any = None,
    evidence: Any = None,
) -> dict[str, Any] | None:
    """Return a structured error dict for bad arguments, or ``None`` when valid."""
    slug = normalize_name(name)
    if slug is None:
        return {
            "error": "invalid_name",
            "detail": "name must contain letters or digits (it is normalized to kebab-case)",
        }
    if memory_type not in MEMORY_TYPES:
        return {
            "error": "invalid_type",
            "detail": f"type must be one of {', '.join(MEMORY_TYPES)}",
        }
    if not isinstance(description, str) or not description.strip():
        return {"error": "empty_description", "detail": "description must be a non-empty string"}
    if not isinstance(content, str) or not content.strip():
        return {"error": "empty_content", "detail": "content must be a non-empty string"}
    provenance_error = _validate_provenance(source, evidence)
    if provenance_error is not None:
        return provenance_error
    issues = find_memory_safety_issues(description, content, evidence or "")
    if issues:
        return {
            "error": "sensitive_content",
            "detail": "memory was not saved because it appears to contain a secret or credential",
            "rules": [issue.rule for issue in issues],
        }
    if is_fenced(slug, memory_type, description):
        return {
            "error": "demo_content",
            "detail": (
                "demo, sample and synthetic runs and repositories are not kept in long-term memory"
            ),
        }
    return None


def normalize_recall_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_RECALL_LIMIT
    limit = int(value)
    return min(max(limit, 0), MAX_RECALL_LIMIT)


__all__ = [
    "DEFAULT_RECALL_LIMIT",
    "MAX_RECALL_LIMIT",
    "normalize_name",
    "normalize_recall_limit",
    "validate_remember_args",
]
