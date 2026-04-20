"""Log compaction utilities — deduplication, count grouping, and error taxonomy.

When a service emits hundreds of log lines during a failure, the LLM only sees
a shallow slice due to hard caps (e.g. 50 logs, 20 errors).  A burst of 48
identical timeout errors consumes 48 of 50 available slots, pushing distinct
errors off the edge.

This module provides two layers of compaction applied *before* the caps:

Phase 1 — **Deduplication + Count Grouping**
  Group identical or near-identical log lines (same message + log_level) into
  single entries with ``count``, ``first_seen``, and ``last_seen``.

Phase 2 — **Structured Error Taxonomy**
  Pre-process fetched logs into an aggregate structure grouped by error type,
  returning a taxonomy summary with representative samples so the LLM receives
  a complete picture of *all* error types across the full fetched log set.

Both functions are pure (no I/O, no LLM calls) and operate on the list-of-dict
log format already used by ``tracer_logs.py`` and ``grafana_actions.py``.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Patterns that vary across otherwise-identical log lines
_VARIABLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
    ),  # UUIDs
    re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s]*"),  # ISO timestamps
    re.compile(r"\b\d{10,13}\b"),  # epoch millis / nanos
    re.compile(r"\b\d+\.\d+\.\d+\.\d+(:\d+)?\b"),  # IP addresses (with optional port)
    re.compile(r"\b0x[0-9a-fA-F]+\b"),  # hex addresses
    re.compile(r"\b\d+(\.\d+)?\s*(ms|s|sec|seconds|bytes|KB|MB|GB)\b"),  # metric values
]


def _normalize_message(message: str) -> str:
    """Collapse variable tokens so near-identical messages share a key.

    >>> _normalize_message("Timeout after 30s connecting to 10.0.0.1:5432")
    'Timeout after <NUM> connecting to <ADDR>'
    """
    normalized = message
    for pattern in _VARIABLE_PATTERNS:
        normalized = pattern.sub("<*>", normalized)
    return normalized.strip()


def _log_sort_key(log: dict[str, Any]) -> str:
    """Return a comparable timestamp string (best-effort)."""
    return str(log.get("timestamp", "") or log.get("first_seen", "") or "")


# ---------------------------------------------------------------------------
# Phase 1 — Deduplication + Count Grouping
# ---------------------------------------------------------------------------


def deduplicate_logs(
    logs: list[dict[str, Any]],
    *,
    max_output: int | None = None,
) -> list[dict[str, Any]]:
    """Group identical / near-identical log lines, preserving time range.

    Each input log is expected to have at least a ``message`` key; ``log_level``
    and ``timestamp`` are used when present.

    Returns a list of *compacted* log dicts sorted by ``first_seen`` (ascending),
    each containing:

    - ``message``      — representative (first-seen) message text
    - ``log_level``    — original log level
    - ``count``        — number of occurrences in the input
    - ``first_seen``   — earliest timestamp in the group
    - ``last_seen``    — latest timestamp in the group

    If *max_output* is given the result is truncated **after** grouping so that
    high-count bursts no longer steal slots from unique messages.
    """
    if not logs:
        return []

    groups: dict[str, dict[str, Any]] = {}

    for log in logs:
        message = log.get("message", "")
        log_level = str(log.get("log_level", "") or "").upper()
        timestamp = str(log.get("timestamp", "") or "")

        # Build grouping key from normalized message + level
        key = f"{log_level}::{_normalize_message(message)}"

        if key in groups:
            entry = groups[key]
            entry["count"] += 1
            if timestamp and (not entry["first_seen"] or timestamp < entry["first_seen"]):
                entry["first_seen"] = timestamp
            if timestamp and (not entry["last_seen"] or timestamp > entry["last_seen"]):
                entry["last_seen"] = timestamp
        else:
            groups[key] = {
                "message": message,
                "log_level": log_level,
                "count": 1,
                "first_seen": timestamp,
                "last_seen": timestamp,
            }

    # Sort groups: errors first, then by first_seen ascending
    result = sorted(groups.values(), key=_log_sort_key)

    if max_output is not None:
        result = result[:max_output]

    return result


# ---------------------------------------------------------------------------
# Phase 2 — Structured Error Taxonomy
# ---------------------------------------------------------------------------

# Broad error-type buckets derived from the message text
_ERROR_TYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ConnectionTimeout", re.compile(r"timeout|timed?\s*out", re.IGNORECASE)),
    ("ConnectionRefused", re.compile(r"connection\s*(refused|reset|closed)", re.IGNORECASE)),
    ("DNSResolution", re.compile(r"dns|name\s*resolution|resolve\s*host", re.IGNORECASE)),
    (
        "AuthenticationError",
        re.compile(r"auth(entication|orization)?\s*(fail|error|denied)|401|403", re.IGNORECASE),
    ),
    (
        "OutOfMemory",
        re.compile(r"(out\s*of\s*memory|oom\s*kill|memory\s*(error|exceed|limit))", re.IGNORECASE),
    ),
    ("DiskFull", re.compile(r"(no\s*space|disk\s*full|storage\s*(full|limit))", re.IGNORECASE)),
    ("RateLimited", re.compile(r"rate\s*limit|throttl|429", re.IGNORECASE)),
    (
        "SchemaValidation",
        re.compile(
            r"(schema|validation|missing\s*field|invalid\s*(field|column|type))", re.IGNORECASE
        ),
    ),
    (
        "NullReference",
        re.compile(
            r"(null\s*pointer|none\s*type|attribute\s*error|nil\s*reference)", re.IGNORECASE
        ),
    ),
    (
        "PermissionDenied",
        re.compile(r"permission\s*denied|access\s*denied|forbidden", re.IGNORECASE),
    ),
    (
        "ResourceNotFound",
        re.compile(r"(not\s*found|404|no\s*such\s*(file|key|bucket))", re.IGNORECASE),
    ),
    (
        "SyntaxError",
        re.compile(r"(syntax\s*error|parse\s*error|unexpected\s*token)", re.IGNORECASE),
    ),
    (
        "ImportError",
        re.compile(r"(import\s*error|module\s*not\s*found|no\s*module\s*named)", re.IGNORECASE),
    ),
    ("Exception", re.compile(r"exception|traceback|stack\s*trace", re.IGNORECASE)),
]


def _classify_error_type(message: str) -> str:
    """Return the first matching error-type bucket, or ``'Unknown'``."""
    for label, pattern in _ERROR_TYPE_PATTERNS:
        if pattern.search(message):
            return label
    return "Unknown"


def _extract_components(message: str) -> list[str]:
    """Best-effort extraction of affected component names from a log message.

    Looks for common patterns like ``service=foo``, host/path segments, and
    quoted identifiers.
    """
    components: list[str] = []

    # key=value patterns (service=foo, host=bar, db=baz, …)
    for match in re.finditer(
        r"(?:service|host|component|db|table|queue|topic|bucket)=([^\s,;]+)", message, re.IGNORECASE
    ):
        components.append(match.group(1))

    # Quoted identifiers ("upstream-api", 'db-pool')
    for match in re.finditer(r"""['"]([a-zA-Z][a-zA-Z0-9_.-]{2,})['"]""", message):
        val = match.group(1)
        if val not in components:
            components.append(val)

    return components[:5]  # cap to avoid noise


def build_error_taxonomy(
    logs: list[dict[str, Any]],
    *,
    max_samples: int = 5,
) -> dict[str, Any]:
    """Build a structured error taxonomy from a list of log entries.

    Groups logs by detected error type and returns an aggregate summary
    containing representative samples and metadata.

    Args:
        logs: Raw log entries (each dict must have at least ``message``).
        max_samples: Maximum raw sample messages to include per error type.

    Returns:
        Dictionary with the following keys:

        - ``error_taxonomy``: list of error-type groups, each with
          ``error_type``, ``count``, ``affected_components``,
          ``sample_message``, ``first_seen``, ``last_seen``,
          ``sample_messages``.
        - ``total_logs_fetched``: total input count.
        - ``distinct_error_types``: number of unique error type buckets.
        - ``raw_samples``: a few representative raw messages across all types.
    """
    if not logs:
        return {
            "error_taxonomy": [],
            "total_logs_fetched": 0,
            "distinct_error_types": 0,
            "raw_samples": [],
        }

    buckets: dict[str, dict[str, Any]] = {}

    for log in logs:
        message = log.get("message", "")
        timestamp = str(log.get("timestamp", "") or "")
        error_type = _classify_error_type(message)

        if error_type not in buckets:
            buckets[error_type] = {
                "error_type": error_type,
                "count": 0,
                "affected_components": [],
                "sample_message": message,
                "first_seen": timestamp,
                "last_seen": timestamp,
                "sample_messages": [],
            }

        bucket = buckets[error_type]
        bucket["count"] += 1

        if timestamp and (not bucket["first_seen"] or timestamp < bucket["first_seen"]):
            bucket["first_seen"] = timestamp
        if timestamp and (not bucket["last_seen"] or timestamp > bucket["last_seen"]):
            bucket["last_seen"] = timestamp

        # Collect unique sample messages (up to max_samples)
        if len(bucket["sample_messages"]) < max_samples:
            normalized = _normalize_message(message)
            existing_normalized = {_normalize_message(m) for m in bucket["sample_messages"]}
            if normalized not in existing_normalized:
                bucket["sample_messages"].append(message)

        # Collect affected components
        for comp in _extract_components(message):
            if comp not in bucket["affected_components"]:
                bucket["affected_components"].append(comp)

    taxonomy = sorted(buckets.values(), key=lambda b: b["count"], reverse=True)

    # Build a small set of raw sample messages across all types
    raw_samples: list[str] = []
    for bucket in taxonomy:
        for msg in bucket["sample_messages"][:2]:
            if msg not in raw_samples:
                raw_samples.append(msg)
            if len(raw_samples) >= 10:
                break
        if len(raw_samples) >= 10:
            break

    return {
        "error_taxonomy": taxonomy,
        "total_logs_fetched": len(logs),
        "distinct_error_types": len(taxonomy),
        "raw_samples": raw_samples,
    }


# ---------------------------------------------------------------------------
# Convenience: combined compaction
# ---------------------------------------------------------------------------


def compact_logs(
    logs: list[dict[str, Any]],
    *,
    max_output: int = 50,
    max_samples: int = 5,
) -> dict[str, Any]:
    """Apply both deduplication and taxonomy in one call.

    Returns a dict with:
    - ``compacted_logs``:  deduplicated log list (Phase 1)
    - ``error_taxonomy``:  structured taxonomy dict (Phase 2, errors only)
    - ``total_raw``:       count of input logs before compaction
    """
    error_keywords = ("error", "fail", "exception", "traceback")

    error_logs = [
        log
        for log in logs
        if any(kw in str(log.get("message", "")).lower() for kw in error_keywords)
        or "error" in str(log.get("log_level", "")).lower()
    ]

    return {
        "compacted_logs": deduplicate_logs(logs, max_output=max_output),
        "error_taxonomy": build_error_taxonomy(error_logs, max_samples=max_samples),
        "total_raw": len(logs),
    }
