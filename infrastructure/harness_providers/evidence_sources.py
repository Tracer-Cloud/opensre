"""Preferred evidence sources and metric query drafts (by ask kind / service).

Core classifies ask kinds (``metric_read``, …) and owns *when* an unformed
metric answer needs a draft fence and *when* a goal is about people-cohort
identity — but it must not name vendor integration ids, own draft text, know
which tools run a query, or read a vendor's observations. Each analytics/vendor
package opts in here at boot. Nothing is preferred and core uses a generic
fence until a vendor registers.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

# ── Preferred evidence sources ────────────────────────────────────────────

_preferred_evidence_sources: dict[str, tuple[str, ...]] = {}


def register_preferred_evidence_source(kind: str, *service_ids: str) -> None:
    """Opt service id(s) into satisfying asks of ``kind`` (append, dedupe).

    Vendors call this from their own modules. The composition root must not
    invent a default vendor list in core — only wire who opts in.
    """
    if not service_ids:
        return
    current = _preferred_evidence_sources.get(kind, ())
    merged = list(current)
    for service_id in service_ids:
        if service_id and service_id not in merged:
            merged.append(service_id)
    _preferred_evidence_sources[kind] = tuple(merged)


def preferred_evidence_sources_for(kind: str) -> tuple[str, ...]:
    """Return preferred integration ids for ``kind``, or ``()`` when none opted in."""
    return _preferred_evidence_sources.get(kind, ())


def preferred_evidence_sources_by_kind() -> dict[str, tuple[str, ...]]:
    """Return a copy of every kind → preferred service ids map."""
    return {kind: ids for kind, ids in _preferred_evidence_sources.items() if ids}


def clear_preferred_evidence_sources() -> None:
    _preferred_evidence_sources.clear()


# ── Metric query drafts + vendor cohort resolvers ─────────────────────────

MetricCohortResolvedFn = Callable[[Any, str], bool]
"""``(evidence, reply) -> True`` when a vendor cohort is live-resolved."""


@dataclass(frozen=True, slots=True)
class MetricQueryDraft:
    """Draft fences one analytics source offers when no live query formed."""

    count: str
    cohort: str | None
    priority: int


_metric_query_drafts: dict[str, MetricQueryDraft] = {}
_metric_cohort_resolvers: dict[str, MetricCohortResolvedFn] = {}
_metric_query_tools: dict[str, frozenset[str]] = {}
_metric_discovery_targets: dict[str, frozenset[str]] = {}


def _require_service_id(service_id: str, *, accessor: str) -> str:
    """Return the trimmed id, or raise — a blank id is a registration bug, not input."""
    key = (service_id or "").strip()
    if not key:
        raise ValueError(f"{accessor} needs a non-empty service_id")
    return key


def register_metric_query_tools(service_id: str, tools: Collection[str]) -> None:
    """Register the tool names that run a live query for this source.

    Without this, core falls back to shape rules that recognise no vendor, so
    the source's real queries read as "no metric query ran".
    """
    key = _require_service_id(service_id, accessor="register_metric_query_tools")
    names = frozenset(name.strip().lower() for name in tools if name and name.strip())
    if not names:
        raise ValueError(f"register_metric_query_tools({key!r}) needs at least one tool name")
    _metric_query_tools[key] = _metric_query_tools.get(key, frozenset()) | names


def register_discovery_targets(service_id: str, targets: Collection[str]) -> None:
    """Register the bridge targets that only explore schema for this source."""
    key = _require_service_id(service_id, accessor="register_discovery_targets")
    names = frozenset(name.strip().lower() for name in targets if name and name.strip())
    if not names:
        raise ValueError(f"register_discovery_targets({key!r}) needs at least one target")
    _metric_discovery_targets[key] = _metric_discovery_targets.get(key, frozenset()) | names


def registered_metric_query_tools() -> frozenset[str]:
    """Every tool name any source registered as running a live query."""
    return frozenset().union(*_metric_query_tools.values()) if _metric_query_tools else frozenset()


def registered_discovery_targets() -> frozenset[str]:
    """Every bridge target any source registered as schema discovery."""
    if not _metric_discovery_targets:
        return frozenset()
    return frozenset().union(*_metric_discovery_targets.values())


def register_metric_query_draft(
    service_id: str,
    *,
    count_draft: str,
    cohort_draft: str | None = None,
    priority: int = 50,
) -> None:
    """Register the draft fence(s) this analytics source owns.

    ``count_draft`` / ``cohort_draft`` are full markdown fences. ``cohort_draft``
    is optional — omit it when the vendor has no cohort/signup template. Lower
    ``priority`` wins when several registered sources are connected at once.
    """
    key = _require_service_id(service_id, accessor="register_metric_query_draft")
    count = (count_draft or "").strip()
    if not count:
        raise ValueError(f"register_metric_query_draft({key!r}) needs a non-empty count_draft")
    cohort = (
        cohort_draft.strip() if isinstance(cohort_draft, str) and cohort_draft.strip() else None
    )
    _metric_query_drafts[key] = MetricQueryDraft(count=count, cohort=cohort, priority=priority)


def register_metric_cohort_resolver(service_id: str, resolver: MetricCohortResolvedFn) -> None:
    """Register how this source decides a cohort is resolved from gather evidence.

    Used after a live query ran: core asks whether identity is still open so it
    can keep the draft fence. The vendor owns observation parsers; core must not.
    """
    key = _require_service_id(service_id, accessor="register_metric_cohort_resolver")
    _metric_cohort_resolvers[key] = resolver


def metric_query_draft_for(
    service_ids: tuple[str, ...],
    *,
    cohort_goal: bool = False,
) -> str | None:
    """Return the registered draft for the highest-priority connected source.

    Ranked by registration priority, then service id — never by the order the
    caller happened to assemble ``service_ids``, which made the winner depend
    on an unrelated tuple.
    """
    matches = [
        (draft, key)
        for key in {str(raw or "").strip() for raw in service_ids}
        if key and (draft := _metric_query_drafts.get(key)) is not None
    ]
    if not matches:
        return None
    draft, _key = min(matches, key=lambda pair: (pair[0].priority, pair[1]))
    if cohort_goal and draft.cohort is not None:
        return draft.cohort
    return draft.count


def metric_cohort_resolved_for(
    service_ids: tuple[str, ...],
    evidence: Any,
    reply: str,
) -> bool | None:
    """Ask registered resolvers whether cohort identity is resolved.

    Returns ``None`` when no opted-in source has a resolver (caller falls back
    to reply-only signals). ``True``/``False`` from the first matching source.
    """
    for raw in service_ids:
        key = str(raw or "").strip()
        if not key:
            continue
        resolver = _metric_cohort_resolvers.get(key)
        if resolver is not None:
            return bool(resolver(evidence, reply))
    return None


def clear_metric_query_drafts() -> None:
    _metric_query_drafts.clear()
    _metric_cohort_resolvers.clear()
    _metric_query_tools.clear()
    _metric_discovery_targets.clear()


def reset() -> None:
    """Clear preferred sources and all metric-draft registries (tests)."""
    clear_preferred_evidence_sources()
    clear_metric_query_drafts()


__all__ = [
    "MetricCohortResolvedFn",
    "MetricQueryDraft",
    "clear_metric_query_drafts",
    "clear_preferred_evidence_sources",
    "metric_cohort_resolved_for",
    "metric_query_draft_for",
    "preferred_evidence_sources_by_kind",
    "preferred_evidence_sources_for",
    "register_discovery_targets",
    "register_metric_cohort_resolver",
    "register_metric_query_draft",
    "register_metric_query_tools",
    "register_preferred_evidence_source",
    "registered_discovery_targets",
    "registered_metric_query_tools",
]
