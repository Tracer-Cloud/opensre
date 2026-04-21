"""Shared typed structures for the investigation loop."""

from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict


class PlanAudit(TypedDict, total=False):
    """Audit metadata recorded for a planning step."""

    loop: int
    tool_budget: int
    planned_count: int
    rerouted: bool
    reroute_reason: str
    inclusion_reasons: list[dict[str, Any]]


class ExecutedHypothesis(TypedDict, total=False):
    """A single planning/execution round recorded in state."""

    actions: list[str]
    rationale: str
    loop_count: int
    source: str
    sources: list[str]
    audit: PlanAudit
