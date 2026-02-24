"""Context building - information that could exist before the incident."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from app.agent.nodes.build_context.models import (
    ContextEvidence,
    ContextSourceError,
    TracerWebRunContext,
)
from app.agent.nodes.build_context.utils import call_safe
from app.agent.state import InvestigationState
from app.agent.tools.tool_actions.tracer.tracer_runs import fetch_failed_run


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


def _fetch_tracer_web_run_context(state: InvestigationState | None = None) -> dict:
    pipeline_name = _extract_pipeline_hint(state)
    return fetch_failed_run(pipeline_name=pipeline_name)


def build_investigation_context(state: InvestigationState) -> dict:
    """Build investigation context in parallel across all sources."""
    context: dict[str, Any] = {}
    errors: list[ContextSourceError] = []
    sources = resolve_context_sources(state)
    registry = get_context_registry()

    valid_sources: list[ContextSource] = []
    for name in sources:
        source = registry.get(name)
        if source:
            valid_sources.append(source)
        else:
            errors.append(ContextSourceError(source=name, message="Unknown context source"))

    if not valid_sources:
        evidence = ContextEvidence(**context, context_errors=errors)
        return evidence.to_state()

    with ThreadPoolExecutor(max_workers=len(valid_sources)) as executor:
        futures = {executor.submit(source.builder, state): source for source in valid_sources}
        for future in as_completed(futures):
            source = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = ContextSourceResult(
                    data={},
                    error=ContextSourceError(source=source.name, message=str(exc)),
                )
            context[source.key] = result.data
            if result.error:
                errors.append(result.error)

    evidence = ContextEvidence(**context, context_errors=errors)
    return evidence.to_state()


def resolve_context_sources(state: InvestigationState) -> list[str]:
    plan_sources = state.get("plan_sources") or []
    if plan_sources:
        return [str(s) for s in plan_sources]

    all_sources = list(get_context_registry().names())

    # Skip tracer_web fetch if the alert comes from an external platform (Datadog, Grafana).
    # resolved_integrations is populated by node_resolve_integrations which runs before build_context.
    resolved = state.get("resolved_integrations") or {}
    external_platforms = {"datadog", "grafana"}
    has_external_platform = any(k in resolved for k in external_platforms)
    if has_external_platform and "tracer_web" in all_sources:
        all_sources.remove("tracer_web")

    return all_sources


def get_context_registry() -> ContextSourceRegistry:
    from app.agent.nodes.build_context.sources.datadog_context import build_context_datadog
    from app.agent.nodes.build_context.sources.grafana_context import build_context_grafana

    return ContextSourceRegistry(
        sources=(
            ContextSource(name="tracer_web", key="tracer_web_run", builder=build_context_tracer_web),
            ContextSource(name="grafana", key="grafana_pre_context", builder=build_context_grafana),
            ContextSource(name="datadog", key="datadog_pre_context", builder=build_context_datadog),
        ),
    )


def _extract_pipeline_hint(state: InvestigationState | None) -> str | None:
    if not state:
        return None

    raw_alert = state.get("raw_alert")
    if isinstance(raw_alert, dict):
        for key in ("pipeline_name", "pipeline", "pipelineName"):
            if value := raw_alert.get(key):
                return str(value)
        for container_key in ("labels", "commonLabels"):
            container = raw_alert.get(container_key)
            if isinstance(container, dict):
                for key in ("pipeline_name", "table"):
                    if value := container.get(key):
                        return str(value)
        alerts = raw_alert.get("alerts")
        if isinstance(alerts, list) and alerts:
            first = alerts[0]
            if isinstance(first, dict):
                alert_labels = first.get("labels")
                if isinstance(alert_labels, dict):
                    for key in ("pipeline_name", "table"):
                        if value := alert_labels.get(key):
                            return str(value)

    pipeline_name = state.get("pipeline_name")
    if pipeline_name and pipeline_name != "Unknown":
        return str(pipeline_name)

    return None
