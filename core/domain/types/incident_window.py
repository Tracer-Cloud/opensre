"""Shared incident time window for time-scoped tool queries.

The agent runs many time-aware tools (log queries, metrics, deploy commit
listings, etc.). Today each tool independently picks a default time range
("last 60 minutes") counted from the agent's wall clock. Two problems
follow.

1. The agent's wall clock is not the same as when the incident actually
   started. An alert that fired three hours before the agent ran will be
   completely outside any "last 60 minutes" query.

2. Different tools default to different ranges, so two tools investigating
   the same incident answer questions about different windows.

This module introduces a single ``IncidentWindow`` value object owned by
run state and populated from the alert's own timestamps in the
``extract_alert`` orchestration step. Once tools start reading from it (deferred to a
follow-up PR) every time-aware tool will agree on the same window.

This file is pure foundation. It does not change any tool's behavior. It
just exposes the resolver, the value object, and the anchor parsers for
the five alert formats the agent receives today.

Window semantics: the window is half-open: ``[since, until)``. Evidence
timestamped exactly at ``since`` is included; evidence at ``until`` is
not. This matches the usual half-open convention used by Datadog,
Grafana, and Loki time-range queries.

All datetimes are timezone-aware and normalised to UTC. Naive timestamps
encountered during parsing are silently treated as UTC. Construction of
``IncidentWindow`` will reject naive inputs to prevent accidental
silent bugs in caller code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from core.domain.types.incident_anchors import (
    SOURCE_ACTIVATED_AT,
    SOURCE_FIRED_AT,
    SOURCE_STARTS_AT,
    coerce_alert_dict,
    extract_anchor,
    parse_iso8601,
)

logger = logging.getLogger(__name__)

# Public constants -----------------------------------------------------------
SCHEMA_VERSION = 1
"""Versions the dict shape returned by ``IncidentWindow.to_dict``.

Bump this whenever the dict layout changes incompatibly so future readers
can branch on it. Backward-compatible additions do not require a bump.
"""

DEFAULT_LOOKBACK_MINUTES = 120
MAX_LOOKBACK_MINUTES = 7 * 24 * 60  # 7 days; bounded to keep MCP/API calls sane
DEFAULT_FORWARD_BUFFER_MINUTES = 10

# Recognised source labels --------------------------------------------------
SOURCE_OVERRIDE = "override"
SOURCE_DEFAULT = "default"


@dataclass(frozen=True)
class IncidentWindow:
    """A resolved ``[since, until)`` window for the current incident.

    The interval is **half-open**: timestamps equal to ``since`` are
    inside the window; timestamps equal to ``until`` are outside.

    The dataclass is frozen and validated at construction. It is not
    possible to build an instance with naive datetimes, with
    ``since >= until``, or with a non-UTC tzinfo (those are normalised to
    UTC in ``__post_init__``). Tools and tests can rely on these
    invariants without re-checking.

    Attributes:
        since: Window start. Always tz-aware UTC. Inclusive.
        until: Window end. Always tz-aware UTC. Exclusive.
        source: Where the anchor came from. One of the ``SOURCE_*``
            constants above. Used in the diagnose narrative and audit
            trail.
        confidence: 0.0 when the source is ``"default"`` (no anchor was
            found), 1.0 when the anchor came from a parsed alert
            timestamp or an explicit override. Future PR 3 may emit
            intermediate values when adapting the window.
    """

    since: datetime
    until: datetime
    source: str
    confidence: float

    def __post_init__(self) -> None:
        # Validate types first to give a useful error before tz/order checks.
        if not isinstance(self.since, datetime):
            raise TypeError(f"since must be a datetime, got {type(self.since).__name__}")
        if not isinstance(self.until, datetime):
            raise TypeError(f"until must be a datetime, got {type(self.until).__name__}")
        if self.since.tzinfo is None or self.until.tzinfo is None:
            raise ValueError(
                "IncidentWindow requires timezone-aware datetimes; "
                "naive datetimes are not allowed. Use datetime.now(UTC) "
                "or attach tzinfo before constructing."
            )
        # Normalise both endpoints to UTC; we override frozen by going through
        # object.__setattr__ which is the dataclass-recommended approach.
        utc_since = self.since.astimezone(UTC)
        utc_until = self.until.astimezone(UTC)
        if utc_since >= utc_until:
            raise ValueError(
                f"IncidentWindow requires since < until "
                f"(got since={utc_since.isoformat()}, until={utc_until.isoformat()}). "
                "A zero-length or inverted window is never valid."
            )
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence}")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a non-empty string")
        # Apply the UTC normalisation, frozen-dataclass style.
        object.__setattr__(self, "since", utc_since)
        object.__setattr__(self, "until", utc_until)

    # -- Serialization ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict for ``AgentState`` storage.

        The returned dict carries a ``_schema_version`` key. Callers
        reconstructing via ``from_dict`` should branch on that field if
        they need to handle multiple versions.
        """
        return {
            "_schema_version": SCHEMA_VERSION,
            "since": _iso_utc(self.since),
            "until": _iso_utc(self.until),
            "source": self.source,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Any) -> IncidentWindow | None:
        """Best-effort reconstruction. Returns ``None`` on bad shape.

        Never raises. If the dict is well-formed but the resulting window
        would violate ``__post_init__`` invariants (e.g. since >= until),
        returns ``None`` rather than letting the error propagate.
        """
        if not isinstance(data, dict):
            return None
        since = parse_iso8601(str(data.get("since", "")))
        until = parse_iso8601(str(data.get("until", "")))
        if since is None or until is None:
            return None
        try:
            return cls(
                since=since,
                until=until,
                source=str(data.get("source", SOURCE_DEFAULT)) or SOURCE_DEFAULT,
                confidence=float(data.get("confidence", 0.0) or 0.0),
            )
        except (TypeError, ValueError):
            return None

    # -- Adaptation ---------------------------------------------------------

    def expanded(self, factor: float = 2.0) -> IncidentWindow:
        """Return a NEW window with the lookback widened by ``factor``.

        ``until`` is preserved (the anchor edge does not move). Only
        ``since`` moves earlier. The widened lookback is clamped to
        ``MAX_LOOKBACK_MINUTES`` so callers cannot accidentally page
        through months of data.

        ``source`` and ``confidence`` are preserved: the underlying anchor
        is still trusted; the expansion only admits the original lookback
        guess was too narrow. The fact of expansion is recorded
        separately in ``state.incident_window_history`` by the caller.

        Raises:
            ValueError: when ``factor <= 1.0``. This method is for
                expansion only; contraction is a separate, deferred
                operation with different semantics.
        """
        if factor <= 1.0:
            raise ValueError(
                f"expanded() requires factor > 1.0 to widen the window (got {factor!r}); "
                "use a separate contraction method to narrow."
            )
        current_lookback_min = (self.until - self.since).total_seconds() / 60.0
        new_lookback_min = min(current_lookback_min * factor, float(MAX_LOOKBACK_MINUTES))
        return IncidentWindow(
            since=self.until - timedelta(minutes=new_lookback_min),
            until=self.until,
            source=self.source,
            confidence=self.confidence,
        )


def _iso_utc(dt: datetime) -> str:
    """Format a UTC ``datetime`` as ISO-8601 with the ``Z`` shorthand."""
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _resolve_anchor_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], tuple[datetime, str] | None]:
    """Return the payload to anchor on plus its pre-computed anchor.

    E2E fixtures such as ``datadog_k8s_alert.json`` store captured evidence
    beside the alert payload: ``{"_meta": ..., "alert": {...}, "evidence": ...}``.
    Anchor parsers expect webhook timestamps at the top level. When the outer
    dict has no anchor but the nested ``alert`` object does, resolve against
    the inner payload.

    The chosen anchor is returned alongside the payload so callers do not run
    ``extract_anchor`` a second time — each call runs all four parsers,
    including the recursive CloudWatch one.
    """
    outer_anchor = extract_anchor(payload)
    if outer_anchor is not None:
        return payload, outer_anchor
    nested = payload.get("alert")
    if not isinstance(nested, dict):
        return payload, None
    nested_anchor = extract_anchor(nested)
    if nested_anchor is not None:
        return nested, nested_anchor
    return payload, None


def resolve_incident_window(
    raw_alert: Any,
    *,
    override: IncidentWindow | None = None,
    lookback_minutes: int = DEFAULT_LOOKBACK_MINUTES,
    forward_buffer_minutes: int = DEFAULT_FORWARD_BUFFER_MINUTES,
    now: datetime | None = None,
) -> IncidentWindow:
    """Resolve the incident time window for the current turn.

    Precedence:
      1. ``override`` always wins. Operators can pin the window and the
         resolver respects it without inspection.
      2. The first anchor parser that finds a timestamp in ``raw_alert``
         determines ``until = anchor + forward_buffer_minutes`` (clamped
         to ``now`` if the anchor is in the future, e.g. due to clock
         skew).
      3. ``since = until - lookback_minutes``.
      4. If no parser succeeds, ``until = now`` and
         ``since = until - lookback_minutes``. ``source`` is recorded as
         ``"default"`` and ``confidence`` is 0.0.

    The resolved window is always clamped to ``MAX_LOOKBACK_MINUTES`` so
    no caller can accidentally page through months of data.

    Logs at INFO when an anchor is found and at DEBUG when falling back
    to the default. Operators can grep these to audit which parser
    matched without attaching a debugger.

    Args:
        raw_alert: The raw alert payload as a dict, JSON string, or
            None. Anchor parsing is best-effort.
        override: A pre-resolved window that should be used as-is.
        lookback_minutes: How far back from ``until`` to look. Capped at
            ``MAX_LOOKBACK_MINUTES``. Non-positive values fall back to
            ``DEFAULT_LOOKBACK_MINUTES``.
        forward_buffer_minutes: How far past the anchor to extend
            ``until``. Useful for catching evidence emitted just after
            the alert condition was detected. Non-positive values use 0.
        now: Optional injection point for the "current time". Tests pass
            this; production callers leave it as None.

    Returns:
        A frozen ``IncidentWindow`` ready to drop into ``AgentState``.
    """
    if override is not None:
        logger.debug(
            "incident_window: override provided source=%s since=%s until=%s",
            override.source,
            _iso_utc(override.since),
            _iso_utc(override.until),
        )
        return override

    lookback_int = int(lookback_minutes) if lookback_minutes else DEFAULT_LOOKBACK_MINUTES
    if lookback_int <= 0:
        lookback_int = DEFAULT_LOOKBACK_MINUTES
    lookback = min(lookback_int, MAX_LOOKBACK_MINUTES)
    buffer_minutes = max(0, int(forward_buffer_minutes or 0))
    current = (now or datetime.now(UTC)).astimezone(UTC)

    payload = coerce_alert_dict(raw_alert)
    anchor_result = _resolve_anchor_payload(payload)[1] if payload else None

    if anchor_result is not None:
        anchor, label = anchor_result
        until = anchor + timedelta(minutes=buffer_minutes)
        # Clock skew protection: the anchor + buffer must not exceed now.
        if until > current:
            until = current
        since = until - timedelta(minutes=lookback)
        # since < until is guaranteed because lookback is clamped to >= 1.
        window = IncidentWindow(
            since=since,
            until=until,
            source=label,
            confidence=1.0,
        )
        logger.info(
            "incident_window: anchored source=%s since=%s until=%s lookback_min=%d",
            window.source,
            _iso_utc(window.since),
            _iso_utc(window.until),
            lookback,
        )
        return window

    # Default fallback.
    until = current
    since = until - timedelta(minutes=lookback)
    window = IncidentWindow(
        since=since,
        until=until,
        source=SOURCE_DEFAULT,
        confidence=0.0,
    )
    logger.debug(
        "incident_window: no anchor found, using default since=%s until=%s lookback_min=%d",
        _iso_utc(window.since),
        _iso_utc(window.until),
        lookback,
    )
    return window


__all__ = [
    "DEFAULT_FORWARD_BUFFER_MINUTES",
    "DEFAULT_LOOKBACK_MINUTES",
    "IncidentWindow",
    "MAX_LOOKBACK_MINUTES",
    "SCHEMA_VERSION",
    "SOURCE_ACTIVATED_AT",
    "SOURCE_DEFAULT",
    "SOURCE_FIRED_AT",
    "SOURCE_OVERRIDE",
    "SOURCE_STARTS_AT",
    "resolve_incident_window",
]
