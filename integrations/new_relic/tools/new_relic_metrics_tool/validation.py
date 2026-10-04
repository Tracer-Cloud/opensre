"""Sanity-check and default-window/limit injection for model-supplied NRQL.

The NRQL text comes straight from the LLM (FR-7), so this module is the one
place that decides whether it is safe to run and fills in the defaults the
model omitted. NRQL has no DML/DDL of its own — every write happens through a
separate GraphQL ``mutation``, never through ``nrql(query: ...)`` — so the
keyword check below is defensive only, guarding against a malformed request
(e.g. a GraphQL mutation string passed in as "nrql") rather than a real NRQL
write capability.
"""

from __future__ import annotations

import math
import re

from config.constants.new_relic import (
    NEW_RELIC_DEFAULT_INCIDENT_LIMIT,
    NEW_RELIC_DEFAULT_WINDOW_MINUTES,
    NEW_RELIC_NRQL_LIMIT_MAX,
    NEW_RELIC_TIMESERIES_MAX_BUCKETS,
)

_SINCE_PATTERN = re.compile(r"\bSINCE\b", re.IGNORECASE)
_LIMIT_PATTERN = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)
_UNIT_SECONDS: dict[str, int] = {
    "second": 1,
    "minute": 60,
    "hour": 3_600,
    "day": 86_400,
    "week": 604_800,
    "month": 2_592_000,
}
_UNIT = r"(second|minute|hour|day|week|month)s?"
_TIMESERIES_SIZE_PATTERN = re.compile(rf"\bTIMESERIES\s+(\d+)\s+{_UNIT}\b", re.IGNORECASE)
_SINCE_AGO_PATTERN = re.compile(rf"\bSINCE\s+(\d+)\s+{_UNIT}\s+AGO\b", re.IGNORECASE)
_UNTIL_AGO_PATTERN = re.compile(rf"\bUNTIL\s+(\d+)\s+{_UNIT}\s+AGO\b", re.IGNORECASE)
#: NRQL string literals — single-quoted, with ``\'`` escapes (mirrors the
#: quoting `_nrql_string_literal` in client.py produces). Stripped before the
#: forbidden-keyword scan so a filter value like ``'%mutation%'`` isn't
#: mistaken for the keyword appearing in the query's own syntax.
_STRING_LITERAL_PATTERN = re.compile(r"'(?:[^'\\]|\\.)*'")

#: Defensive-only keywords: NRQL has no mutation syntax, so any of these
#: appearing in a supposed NRQL string signals a malformed/smuggled request.
#: Word-boundary anchored so a legitimate identifier that merely contains one
#: of these words (e.g. a ``MutationAuditEvent`` event type) isn't rejected.
_FORBIDDEN_KEYWORDS: tuple[str, ...] = (
    "mutation",
    "delete",
    "drop",
    "insert",
    "update",
    "alter",
)
_FORBIDDEN_KEYWORD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (keyword, re.compile(rf"\b{keyword}\b")) for keyword in _FORBIDDEN_KEYWORDS
)


def _mask_string_literals(text: str) -> str:
    """Length-preserving mask of NRQL string literals.

    Replaces only the literal's *contents*, keeping quotes and overall
    length identical to *text* so match offsets found in the masked copy
    point at the same positions in the original — a filter value like
    ``'%LIMIT 5%'`` or ``'%mutation%'`` can no longer be mistaken for a real
    clause or keyword appearing in the query's own syntax.
    """
    return _STRING_LITERAL_PATTERN.sub(lambda m: "'" + "x" * (len(m.group(0)) - 2) + "'", text)


def validate_nrql(nrql: str) -> tuple[bool, str]:
    """Return ``(is_valid, error)`` for a raw NRQL string received from the model."""
    text = str(nrql or "").strip()
    if not text:
        return False, "nrql query cannot be empty."
    lowered = text.lower()
    if not lowered.startswith("select"):
        return False, "nrql query must be a read-only SELECT statement."
    # Scan outside string literals only — a filter value containing one of
    # these words (e.g. WHERE url LIKE '%mutation%') is not a mutation attempt.
    scannable = _mask_string_literals(lowered)
    for keyword, pattern in _FORBIDDEN_KEYWORD_PATTERNS:
        if pattern.search(scannable):
            return False, f"nrql query must not contain '{keyword}'."
    return True, ""


def apply_default_window_and_limit(
    nrql: str,
    *,
    since_minutes: int = NEW_RELIC_DEFAULT_WINDOW_MINUTES,
    limit: int = NEW_RELIC_DEFAULT_INCIDENT_LIMIT,
) -> str:
    """Inject a default ``SINCE``/``LIMIT`` when the model's NRQL omits them.

    The 5s NRQL-via-API timeout (NFR-7) makes an unbounded query a near
    guaranteed failure on a large account, so both clauses are added rather
    than left to the model's discretion. An explicit ``LIMIT`` above the
    vendor's own ceiling is clamped down to it, never raised. Clauses are
    located in a literal-masked copy of the text so a keyword inside a quoted
    filter value (e.g. ``'%SINCE yesterday%'``) is never mistaken for a real
    clause.
    """
    text = nrql.strip()

    masked = _mask_string_literals(text)
    if not _SINCE_PATTERN.search(masked):
        since_clause = f"SINCE {int(since_minutes)} minutes ago"
        limit_match = _LIMIT_PATTERN.search(masked)
        if limit_match is None:
            text = f"{text} {since_clause}"
        else:
            # NRQL clause order requires SINCE before LIMIT — splice it in
            # ahead of the existing clause rather than appending at the end.
            start = limit_match.start()
            text = f"{text[:start]}{since_clause} {text[start:]}"
        masked = _mask_string_literals(text)

    limit_match = _LIMIT_PATTERN.search(masked)
    if limit_match is None:
        text = f"{text} LIMIT {int(limit)}"
    elif int(limit_match.group(1)) > NEW_RELIC_NRQL_LIMIT_MAX:
        start, end = limit_match.span()
        text = f"{text[:start]}LIMIT {NEW_RELIC_NRQL_LIMIT_MAX}{text[end:]}"

    return text


def extract_limit(nrql: str) -> int | None:
    """Return the numeric ``LIMIT`` clause in *nrql*, or ``None`` if absent."""
    match = _LIMIT_PATTERN.search(_mask_string_literals(nrql))
    return None if match is None else int(match.group(1))


def _relative_seconds(match: re.Match[str] | None) -> int | None:
    if match is None:
        return None
    return int(match.group(1)) * _UNIT_SECONDS[match.group(2).lower()]


def clamp_timeseries_buckets(nrql: str) -> str:
    """Widen an explicit ``TIMESERIES <n> <unit>`` bucket that would exceed NRQL's cap.

    NRQL rejects a ``TIMESERIES`` query producing more than
    ``NEW_RELIC_TIMESERIES_MAX_BUCKETS`` buckets (e.g. ``TIMESERIES 10 minutes``
    over ``SINCE 7 days ago`` is 1008). When the window is a relative
    ``SINCE <n> <unit> ago`` (optionally ``UNTIL <n> <unit> ago``), the bucket
    is rewritten to the finest size that fits; absolute windows are left as-is.
    """
    masked = _mask_string_literals(nrql)
    bucket_match = _TIMESERIES_SIZE_PATTERN.search(masked)
    since_seconds = _relative_seconds(_SINCE_AGO_PATTERN.search(masked))
    bucket_seconds = _relative_seconds(bucket_match)
    if bucket_match is None or since_seconds is None or not bucket_seconds:
        return nrql
    window_seconds = since_seconds - (_relative_seconds(_UNTIL_AGO_PATTERN.search(masked)) or 0)
    if math.ceil(window_seconds / bucket_seconds) <= NEW_RELIC_TIMESERIES_MAX_BUCKETS:
        return nrql
    min_seconds = math.ceil(window_seconds / NEW_RELIC_TIMESERIES_MAX_BUCKETS)
    bucket = (
        f"{min_seconds} seconds"
        if min_seconds < _UNIT_SECONDS["minute"]
        else f"{math.ceil(min_seconds / _UNIT_SECONDS['minute'])} minutes"
    )
    start, end = bucket_match.span()
    return f"{nrql[:start]}TIMESERIES {bucket}{nrql[end:]}"
