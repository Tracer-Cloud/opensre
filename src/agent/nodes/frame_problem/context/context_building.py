"""Context building - information that could exist before the incident."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from src.agent.nodes.frame_problem.context.models import (
    ContextEvidence,
    ContextSourceError,
    TracerWebRunContext,
)
from src.agent.nodes.frame_problem.context.utils import call_safe
from src.agent.state import InvestigationState
from src.agent.tools.tool_actions.actions_tracer_web import (
    build_tracer_run_url as _build_tracer_run_url,
)
from src.agent.tools.tool_actions.actions_tracer_web import (
    fetch_failed_run_context,
)

DEFAULT_CONTEXT_SOURCES = ("tracer_web",)


@dataclass(frozen=True)
class ContextSourceResult:
    data: dict[str, Any]
    error: ContextSourceError | None = None


@dataclass(frozen=True)
class ContextSource:
    name: str
    key: str
    builder: Callable[[InvestigationState], ContextSourceResult]


class ContextSourceRegistry:
    def __init__(self, sources: Iterable[ContextSource]) -> None:
        self._sources = {source.name: source for source in sources}

    def get(self, name: str) -> ContextSource | None:
        return self._sources.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(self._sources.keys())


def build_context_tracer_web(state: InvestigationState) -> ContextSourceResult:
    """Build context from Tracer Web App (metadata about failed run)."""
    outcome = call_safe(_fetch_tracer_web_run_context, state=state)
    if outcome.error:
        error = ContextSourceError(source="tracer_web", message=outcome.error)
        data = TracerWebRunContext(found=False, error=outcome.error).model_dump(exclude_none=True)
        return ContextSourceResult(data=data, error=error)

    if outcome.result is None:
        error = ContextSourceError(source="tracer_web", message="No context returned")
        data = TracerWebRunContext(found=False, error=error.message).model_dump(exclude_none=True)
        return ContextSourceResult(data=data, error=error)

    data = TracerWebRunContext.model_validate(outcome.result).model_dump(exclude_none=True)
    return ContextSourceResult(data=data)


def build_tracer_run_url(pipeline_name: str, trace_id: str | None) -> str | None:
    """Backward-compatible wrapper for tracer run URL building."""
    return _build_tracer_run_url(pipeline_name, trace_id)


def _fetch_tracer_web_run_context(state: InvestigationState | None = None) -> dict:
    """Fetch context (metadata) about a failed run from Tracer Web App."""
    pipeline_name = _extract_pipeline_hint(state)
    context = fetch_failed_run_context(pipeline_name=pipeline_name)
    return context


def build_investigation_context(state: InvestigationState) -> dict:
    """
    Build investigation context (metadata that could exist before incident).

    This includes:
    - Pipeline metadata (name, run name, trace_id)
    - System metadata (instance type, region, user)
    - Run summary (status, cost, tool count)
    - URLs and identifiers

    Does NOT include:
    - Failed jobs/tools (runtime evidence)
    - Error logs (runtime evidence)
    - Metrics (runtime evidence)
    """
    context: dict[str, Any] = {}
    errors: list[ContextSourceError] = []
    sources = resolve_context_sources(state)
    registry = get_context_registry()

    for source_name in sources:
        source = registry.get(source_name)
        if not source:
            errors.append(
                ContextSourceError(source=source_name, message="Unknown context source")
            )
            continue
        result = source.builder(state)
        context[source.key] = result.data
        if result.error:
            errors.append(result.error)

    evidence = ContextEvidence(**context, context_errors=errors)
    return evidence.to_state()


def resolve_context_sources(state: InvestigationState) -> list[str]:
    """Resolve context sources from state or environment."""
    plan_sources = state.get("plan_sources") or []
    if plan_sources:
        return [str(source) for source in plan_sources]

    env_sources = os.getenv("FRAME_PROBLEM_CONTEXT_SOURCES")
    if env_sources:
        return [source.strip() for source in env_sources.split(",") if source.strip()]

    return list(DEFAULT_CONTEXT_SOURCES)


def get_context_registry() -> ContextSourceRegistry:
    return ContextSourceRegistry(
        sources=(
            ContextSource(
                name="tracer_web",
                key="tracer_web_run",
                builder=build_context_tracer_web,
            ),
        ),
    )


def _extract_pipeline_hint(state: InvestigationState | None) -> str | None:
    if not state:
        return None

    raw_alert = state.get("raw_alert")
    if isinstance(raw_alert, dict):
        for key in ("pipeline_name", "pipeline", "pipelineName"):
            value = raw_alert.get(key)
            if value:
                return str(value)
        labels = raw_alert.get("labels") if isinstance(raw_alert.get("labels"), dict) else None
        if labels:
            for key in ("pipeline_name", "table"):
                value = labels.get(key)
                if value:
                    return str(value)
        common_labels = (
            raw_alert.get("commonLabels") if isinstance(raw_alert.get("commonLabels"), dict) else None
        )
        if common_labels:
            for key in ("pipeline_name", "table"):
                value = common_labels.get(key)
                if value:
                    return str(value)
        alerts = raw_alert.get("alerts")
        if isinstance(alerts, list) and alerts:
            first_alert = alerts[0]
            if isinstance(first_alert, dict):
                alert_labels = (
                    first_alert.get("labels") if isinstance(first_alert.get("labels"), dict) else None
                )
                if alert_labels:
                    for key in ("pipeline_name", "table"):
                        value = alert_labels.get(key)
                        if value:
                            return str(value)

    affected_table = state.get("affected_table")
    if affected_table and affected_table != "Unknown":
        return str(affected_table)

    return None
