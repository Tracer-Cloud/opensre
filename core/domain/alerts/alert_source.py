"""Alert source resolution and tool-source routing helpers.

Naming convention:

- ``alert_source`` — vendor/format key on the incoming alert payload (e.g.
  ``"grafana"``, ``"eks"``). Keys the :func:`alert_source_routing` table.
- ``tool source`` — integration key matching ``tool.source`` (e.g.
  ``"grafana"``, ``"ec2"``, ``"cloudtrail"``). Values in routing tuples.

Each ``AlertSourceRouting`` entry carries two tool-source lists:

- ``relevance_tool_sources`` — broad prioritization for alert-driven tool use.
- ``seed_tool_sources`` — narrower subset worth auto-calling first.
  Expensive or context-dependent tools stay out of seeding.

Core owns only the mechanism (the dataclass, the registries, and the
resolver functions). The actual table — which alert source routes to which
tool sources, and which keywords alias to which source — is vendor/catalog
data registered by :mod:`integrations.alert_source_catalog` from
``integrations/harness_adapters.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from core.domain.alerts.fields import iter_alert_blocks

AlertSourceDetector = Callable[[dict[str, Any]], "str | None"]
"""A detector inspects the raw alert dict and returns a vendor key, if any.

Vendor-specific sniffing (label heuristics, URL patterns, etc.) lives in the
owning ``integrations/<vendor>/`` package and is wired in via
:func:`register_alert_source_detector` from ``integrations/harness_adapters.py``
— core stays free of hardcoded vendor heuristics.
"""

_alert_source_detectors: list[AlertSourceDetector] = []


def register_alert_source_detector(detector: AlertSourceDetector) -> None:
    """Register a vendor alert-source detector, run in registration order."""
    _alert_source_detectors.append(detector)


def clear_alert_source_detectors() -> None:
    """Remove all registered detectors (tests)."""
    _alert_source_detectors.clear()


@dataclass(frozen=True)
class AlertSourceRouting:
    relevance_tool_sources: tuple[str, ...]
    seed_tool_sources: tuple[str, ...]


def routing(
    relevance_tool_sources: tuple[str, ...],
    seed_tool_sources: tuple[str, ...],
) -> AlertSourceRouting:
    """Build an :class:`AlertSourceRouting` entry (factory for catalog modules)."""
    return AlertSourceRouting(
        relevance_tool_sources=relevance_tool_sources,
        seed_tool_sources=seed_tool_sources,
    )


# Populated by vendor/cross-vendor catalogs (e.g.
# ``integrations/alert_source_catalog.py``) registered from
# ``integrations/harness_adapters.py`` — core has no hardcoded vendor table.
_alert_source_routing: dict[str, AlertSourceRouting] = {}


def register_alert_source_routing(source: str, routing: AlertSourceRouting) -> None:
    """Register (or replace) the routing entry for one alert-source key."""
    _alert_source_routing[source.strip().lower()] = routing


def clear_alert_source_routing() -> None:
    """Remove all registered alert-source routing entries (tests)."""
    _alert_source_routing.clear()


def alert_source_routing() -> dict[str, AlertSourceRouting]:
    """Return a snapshot of the currently registered routing table."""
    return dict(_alert_source_routing)


# Generic fallback sources: useful, but never primary when incident-specific
# integrations match. Vendor packages register their own source string(s)
# from integrations/harness_adapters.py — core has no hardcoded vendor list.
_secondary_tool_sources: set[str] = set()


def register_secondary_tool_source(source: str) -> None:
    """Register a tool source as a generic fallback (never primary)."""
    _secondary_tool_sources.add(source)


def clear_secondary_tool_sources() -> None:
    """Remove all registered secondary tool sources (tests)."""
    _secondary_tool_sources.clear()


def secondary_tool_sources() -> frozenset[str]:
    """Return the currently registered generic fallback tool sources."""
    return frozenset(_secondary_tool_sources)


DB_KEYWORDS: tuple[str, ...] = ("database", "db connection", "connection pool")

# Populated by vendor/cross-vendor catalogs (e.g.
# ``integrations/alert_source_catalog.py``) registered from
# ``integrations/harness_adapters.py`` — core has no hardcoded vendor keywords.
_source_aliases: dict[str, tuple[str, ...]] = {}


def register_source_aliases(source: str, aliases: tuple[str, ...]) -> None:
    """Register (or replace) the keyword-alias set for one tool source."""
    _source_aliases[source.strip().lower()] = aliases


def clear_source_aliases() -> None:
    """Remove all registered source-alias entries (tests)."""
    _source_aliases.clear()


def source_aliases() -> dict[str, tuple[str, ...]]:
    """Return a snapshot of the currently registered alias table."""
    return dict(_source_aliases)


def routing_for_alert_source(alert_source: str) -> AlertSourceRouting | None:
    """Return routing config for a resolved alert vendor key, if known."""
    return _alert_source_routing.get(alert_source.strip().lower())


def primary_sources_for_alert(state: dict[str, Any]) -> tuple[str, ...]:
    """Return the routing entry's ``relevance_tool_sources`` for this alert.

    Used for broad alert-driven tool prioritization; callers surface these
    as ``primary_sources`` in plan audits and prompts.
    """
    entry = routing_for_alert_source(resolve_alert_source(state))
    return entry.relevance_tool_sources if entry is not None else ()


def seed_tool_sources_for_alert(state: dict[str, Any]) -> tuple[str, ...]:
    """Return tool sources worth auto-calling first for this alert."""
    entry = routing_for_alert_source(resolve_alert_source(state))
    return entry.seed_tool_sources if entry is not None else ()


def declared_context_sources(state: dict[str, Any]) -> set[str]:
    """Return explicit context source annotations from the raw alert, if any."""
    raw = state.get("raw_alert")
    if not isinstance(raw, dict):
        return set()
    for block in iter_alert_blocks(raw):
        value = block.get("context_sources")
        if isinstance(value, str) and value.strip():
            return {item.strip().lower() for item in value.split(",") if item.strip()}
    return set()


def collect_alert_text(state: dict[str, Any]) -> str:
    """Collect searchable alert text for deterministic source/tool matching."""
    parts: list[str] = [
        str(state.get("alert_name") or ""),
        str(state.get("message") or ""),
    ]
    raw = state.get("raw_alert")
    if isinstance(raw, dict):
        for key in ("alert_name", "title", "message", "text", "error_message", "kube_namespace"):
            value = raw.get(key)
            if isinstance(value, str):
                parts.append(value)
        for block in iter_alert_blocks(raw):
            parts.extend(str(v) for v in block.values() if isinstance(v, (str, int, float)))
    elif isinstance(raw, str):
        parts.append(raw)

    problem_md = state.get("problem_md")
    if isinstance(problem_md, str):
        parts.append(problem_md)

    return " ".join(part for part in parts if part).lower()


def relevant_sources_for_alert(
    state: dict[str, Any],
    candidate_sources: Iterable[str],
) -> list[str]:
    """Select candidate sources relevant to the alert content."""
    secondary = secondary_tool_sources()
    candidates = sorted(source for source in candidate_sources if source not in secondary)
    if not candidates:
        return []

    declared = declared_context_sources(state)
    if declared:
        from_declared = [source for source in candidates if source in declared]
        if from_declared:
            return from_declared

    text = collect_alert_text(state)
    if not text:
        return []

    keyword_map = {source: {source, *_source_aliases.get(source, ())} for source in candidates}
    matched: list[str] = []

    for source, keywords in keyword_map.items():
        if any(keyword in text for keyword in keywords):
            matched.append(source)

    return matched


def resolve_alert_source(state: dict[str, Any]) -> str:
    """Return the alert vendor key used to look up tool-source routing.

    Some vendors (e.g. Grafana managed alerts, which reuse the Alertmanager
    webhook schema) omit ``alert_source`` from the payload and must be
    sniffed from other fields. That sniffing is vendor-specific and lives in
    registered detectors (see :func:`register_alert_source_detector`) rather
    than here, so core has no hardcoded vendor heuristics.
    """
    source = str(state.get("alert_source") or "").lower().strip()
    if source:
        return source
    raw = state.get("raw_alert")
    if isinstance(raw, dict):
        source = str(raw.get("alert_source") or "").lower().strip()
        if source:
            return source
        for detector in _alert_source_detectors:
            detected = detector(raw)
            if detected:
                return detected
    return ""


__all__ = [
    "DB_KEYWORDS",
    "AlertSourceDetector",
    "AlertSourceRouting",
    "alert_source_routing",
    "clear_alert_source_detectors",
    "clear_alert_source_routing",
    "clear_secondary_tool_sources",
    "clear_source_aliases",
    "collect_alert_text",
    "declared_context_sources",
    "primary_sources_for_alert",
    "register_alert_source_detector",
    "register_alert_source_routing",
    "register_secondary_tool_source",
    "register_source_aliases",
    "relevant_sources_for_alert",
    "resolve_alert_source",
    "routing",
    "routing_for_alert_source",
    "secondary_tool_sources",
    "seed_tool_sources_for_alert",
    "source_aliases",
]
