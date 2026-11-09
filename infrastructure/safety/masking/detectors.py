"""Regex detectors for sensitive infrastructure identifiers.

Each detector contributes zero or more ``DetectedIdentifier`` matches when
``find_identifiers(text, policy)`` is called. Contextual detectors (namespace,
cluster, service_name) only match when preceded by a recognized label so
that generic words like ``frontend`` are not mistakenly masked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from infrastructure.safety.masking.policy import (
    IdentifierKind,
    MaskingPolicy,
    compile_extra_patterns,
)


@dataclass(frozen=True)
class DetectedIdentifier:
    """A single identifier found in text."""

    kind: str
    start: int
    end: int
    value: str


# Built-in detectors. Each entry maps an ``IdentifierKind`` to a compiled
# regex. Contextual detectors capture the VALUE in group(1) so we only mask
# the identifier itself (not the preceding label like "kube_namespace:").

_POD_RE = re.compile(r"\b([a-z0-9](?:[-a-z0-9]*[a-z0-9])?-[a-f0-9]{5,10}(?:-[a-z0-9]{3,10})?)\b")
_NAMESPACE_RE = re.compile(
    r"\b(?:kube_namespace|namespace|ns)[=:\s]+([a-z0-9][-a-z0-9]*)\b", re.IGNORECASE
)
_CLUSTER_RE = re.compile(
    r"\b(?:kube_cluster|eks_cluster|cluster(?:_name)?)[=:\s]+"
    r"([a-zA-Z0-9][-a-zA-Z0-9_]{1,})\b",
    re.IGNORECASE,
)
_SERVICE_NAME_RE = re.compile(
    r"\b(?:service|service_name|app|deployment)[=:\s]+([a-zA-Z0-9][-a-zA-Z0-9_]{1,})\b",
    re.IGNORECASE,
)
_HOSTNAME_RE = re.compile(
    r"\b("
    r"kind-[a-z0-9][-a-z0-9]*"  # local kind clusters
    # ec2-style internal hostnames: ip-10-0-1-23.ec2.internal
    # Note: the inner character class excludes '.' so the outer (?:\.LABEL)*
    # has no ambiguity and cannot backtrack exponentially (CodeQL ReDoS).
    r"|ip-\d+-\d+-\d+-\d+(?:\.[a-z0-9][a-z0-9-]*)*"
    # Generic DNS-style: label(.label)+.(tld)
    r"|[a-z0-9][-a-z0-9]*(?:\.[a-z0-9][-a-z0-9]*)+\.(?:com|net|org|io|internal|local|cloud)"
    r")\b",
    re.IGNORECASE,
)
_ACCOUNT_RE = re.compile(r"\b(\d{12})\b")
_IP_RE = re.compile(
    r"\b((?:25[0-5]|2[0-4]\d|[01]?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|[01]?\d?\d)){3})\b"
)
_EMAIL_RE = re.compile(r"\b([\w.+-]+@[\w-]+\.[\w.-]+)\b")


_BUILTIN_DETECTORS: dict[IdentifierKind, re.Pattern[str]] = {
    IdentifierKind.POD: _POD_RE,
    IdentifierKind.NAMESPACE: _NAMESPACE_RE,
    IdentifierKind.CLUSTER: _CLUSTER_RE,
    IdentifierKind.SERVICE_NAME: _SERVICE_NAME_RE,
    IdentifierKind.HOSTNAME: _HOSTNAME_RE,
    IdentifierKind.ACCOUNT_ID: _ACCOUNT_RE,
    IdentifierKind.IP_ADDRESS: _IP_RE,
    IdentifierKind.EMAIL: _EMAIL_RE,
}


def find_identifiers(
    text: str,
    policy: MaskingPolicy,
    compiled_extras: dict[str, re.Pattern[str]] | None = None,
) -> list[DetectedIdentifier]:
    """Return all identifiers found in ``text`` under ``policy``.

    Matches are returned sorted by start position. When two detectors match
    overlapping regions (fully contained *or partially overlapping*), the
    longer earlier match wins so we never corrupt the output in
    ``_apply_replacements``.

    ``compiled_extras`` may be passed by callers that want to compile the
    policy's extra regex patterns once per run instead of on
    every call.
    """
    if not policy.enabled or not text:
        return []

    found: list[DetectedIdentifier] = []

    for kind, pattern in _BUILTIN_DETECTORS.items():
        if not policy.is_kind_enabled(kind):
            continue
        _append_matches(pattern, text, kind, found)

    extras = compiled_extras if compiled_extras is not None else compile_extra_patterns(policy)
    for label, extra in extras.items():
        _append_matches(extra, text, label, found)

    return _resolve_overlaps(found)


def _append_matches(
    pattern: re.Pattern[str],
    text: str,
    kind: str,
    out: list[DetectedIdentifier],
) -> None:
    for match in pattern.finditer(text):
        # Prefer group(1) if defined (contextual detectors), else the full match.
        if match.groups():
            start, end = match.span(1)
            value = match.group(1)
        else:
            start, end = match.span()
            value = match.group()
        if value:
            out.append(DetectedIdentifier(kind=kind, start=start, end=end, value=value))


def _resolve_overlaps(matches: list[DetectedIdentifier]) -> list[DetectedIdentifier]:
    """Drop matches that overlap (fully or partially) a longer earlier match.

    Sort by start ASC, then by length DESC so the longest match at each
    start position is considered first. Kept intervals are non-overlapping and
    start-ordered, so a candidate only needs to compare against the previous
    kept end — O(N) after the sort, not O(N²) against the full kept list.
    """
    if not matches:
        return []
    by_start = sorted(matches, key=lambda m: (m.start, -(m.end - m.start)))
    result: list[DetectedIdentifier] = []
    last_end = -1
    for m in by_start:
        # Overlap with previous kept: last_end > m.start (half-open spans).
        if m.start < last_end:
            continue
        result.append(m)
        last_end = m.end
    return result


__all__ = [
    "DetectedIdentifier",
    "find_identifiers",
]
