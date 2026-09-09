"""Provider-neutral tool for loading trusted runbook guidance."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from config.runbook_sources import RunbookSourceConfig, load_runbook_sources
from core.domain.runbooks import (
    IncidentIdentity,
    RunbookCatalogEntry,
    RunbookDocument,
    RunbookReference,
    RunbookSelection,
    RunbookSource,
    select_runbook,
)
from core.domain.types.tools import ToolSurface
from core.tool import AgentToolContext, SideEffectLevel, availability_view, report_run_error
from core.tool_framework import tool
from infrastructure.harness_providers import resolve_runbook_source
from tools.system.runbook_guidance_tool._evidence import map_runbook_guidance

logger = logging.getLogger(__name__)

_STATUS_LOADED = "loaded"
_STATUS_NOT_FOUND = "not_found"
_STATUS_AMBIGUOUS = "ambiguous"
_STATUS_UNAVAILABLE = "unavailable"
_TOOL_NAME = "load_runbook_guidance"
_TOOL_SOURCE = "knowledge"


def _report_failure(
    exc: Exception,
    *,
    method: str,
    source_name: str = "",
) -> None:
    extras = {"runbook_source": source_name} if source_name else None
    report_run_error(
        exc,
        tool_name=_TOOL_NAME,
        source=_TOOL_SOURCE,
        component="tools.system.runbook_guidance_tool.tool",
        method=method,
        logger=logger,
        extras=extras,
    )


@dataclass(frozen=True, slots=True)
class _SourceBinding:
    config: RunbookSourceConfig
    source: RunbookSource


@dataclass(frozen=True, slots=True)
class _CatalogMatch:
    binding: _SourceBinding
    entry: RunbookCatalogEntry
    selection: RunbookSelection
    revision: str


def _runbook_sources_available(sources: dict[str, dict[str, Any]]) -> bool:
    try:
        configured = load_runbook_sources()
        return any(resolve_runbook_source(source, sources) is not None for source in configured)
    except Exception as exc:
        _report_failure(exc, method="is_available")
        return False


def _result(
    status: str,
    message: str,
    *,
    available: bool = True,
    candidates: list[str] | None = None,
    warnings: list[str] | None = None,
    runbook: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": available,
        "status": status,
        "message": message,
        "candidates": candidates or [],
        "warnings": warnings or [],
    }
    if runbook is not None:
        result["runbook"] = runbook
    return result


def _load_bindings(
    context: AgentToolContext | None,
    source_name: str,
) -> tuple[list[_SourceBinding], list[str]]:
    if context is None:
        return [], ["Runbook source integrations are unavailable in this runtime."]
    try:
        configured = load_runbook_sources()
    except Exception as exc:
        _report_failure(exc, method="load_runbook_sources")
        return [], ["Runbook source configuration could not be loaded."]

    selected = tuple(
        source for source in configured if not source_name or source.name == source_name
    )
    if source_name and not selected:
        return [], [f"Runbook source {source_name!r} is not configured."]

    bindings: list[_SourceBinding] = []
    unavailable: list[str] = []
    integrations = availability_view(context.resolved_integrations)
    for config in selected:
        try:
            provider = resolve_runbook_source(config, integrations)
        except Exception as exc:
            _report_failure(
                exc,
                method="resolve_runbook_source",
                source_name=config.name,
            )
            provider = None
        if provider is None:
            unavailable.append(
                f"Runbook source {config.name!r} is unavailable; verify its integration."
            )
            continue
        bindings.append(_SourceBinding(config=config, source=provider))
    return bindings, unavailable


def _document_payload(
    match: _CatalogMatch | None,
    binding: _SourceBinding,
    document: RunbookDocument,
) -> dict[str, Any]:
    entry = match.entry if match is not None else None
    selection = match.selection if match is not None else None
    return {
        "source_name": binding.config.name,
        "provider": binding.config.provider,
        "repository": binding.config.repository,
        "document_id": document.reference.document_id,
        "title": (entry.title if entry is not None else "") or document.title,
        "path": document.reference.path,
        "revision": document.resolved_revision,
        "url": document.source_uri,
        "content": document.content,
        "truncated": document.truncated,
        "match_reason": selection.reason if selection is not None else "explicit_url",
        "matched_fields": list(selection.matched_fields) if selection is not None else [],
    }


def _fetch_document(
    binding: _SourceBinding,
    reference: RunbookReference,
    *,
    match: _CatalogMatch | None = None,
) -> dict[str, Any]:
    try:
        document = binding.source.fetch_document(reference)
    except Exception as exc:
        _report_failure(
            exc,
            method="RunbookSource.fetch_document",
            source_name=binding.config.name,
        )
        return _result(
            _STATUS_UNAVAILABLE,
            "The selected runbook could not be retrieved.",
            available=False,
            warnings=[f"Runbook source {binding.config.name!r} could not retrieve the document."],
        )
    return _result(
        _STATUS_LOADED,
        "Loaded trusted runbook guidance at an immutable revision.",
        runbook=_document_payload(match, binding, document),
    )


def _load_explicit_url(
    bindings: list[_SourceBinding],
    runbook_url: str,
    warnings: list[str],
) -> dict[str, Any]:
    resolved: list[tuple[_SourceBinding, RunbookReference]] = []
    for binding in bindings:
        try:
            reference = binding.source.resolve_reference(runbook_url)
        except Exception as exc:
            _report_failure(
                exc,
                method="RunbookSource.resolve_reference",
                source_name=binding.config.name,
            )
            warnings.append(f"Runbook source {binding.config.name!r} could not inspect the URL.")
            continue
        if reference is not None:
            resolved.append((binding, reference))

    if not resolved:
        return _result(
            _STATUS_UNAVAILABLE,
            "No configured trusted runbook source accepts this URL.",
            available=False,
            warnings=warnings,
        )
    if len(resolved) > 1:
        candidates = sorted(binding.config.name for binding, _reference in resolved)
        return _result(
            _STATUS_AMBIGUOUS,
            "The runbook URL belongs to more than one configured source.",
            candidates=candidates,
            warnings=warnings,
        )

    binding, reference = resolved[0]
    result = _fetch_document(binding, reference)
    result["warnings"] = [*warnings, *result["warnings"]]
    return result


def _catalog_reference(match: _CatalogMatch) -> RunbookReference:
    return RunbookReference(
        source_name=match.binding.config.name,
        document_id=match.entry.document_id,
        path=match.entry.path,
        requested_revision=match.revision,
    )


def _load_catalog_match(
    bindings: list[_SourceBinding],
    incident: IncidentIdentity,
    warnings: list[str],
) -> dict[str, Any]:
    matches: list[_CatalogMatch] = []
    ambiguous: list[tuple[tuple[int, int, int], tuple[str, ...]]] = []
    readable_catalogs = 0
    manifest_sources = 0

    for binding in bindings:
        if not binding.config.manifest:
            continue
        manifest_sources += 1
        try:
            catalog = binding.source.fetch_catalog()
        except Exception as exc:
            _report_failure(
                exc,
                method="RunbookSource.fetch_catalog",
                source_name=binding.config.name,
            )
            warnings.append(f"Runbook source {binding.config.name!r} could not load its catalog.")
            continue
        readable_catalogs += 1
        selection = select_runbook(catalog.entries, incident)
        if selection.status == _STATUS_AMBIGUOUS:
            ambiguous.append(
                (
                    selection.specificity,
                    tuple(
                        f"{binding.config.name}/{candidate}"
                        for candidate in selection.candidate_ids
                    ),
                )
            )
        elif selection.status == "matched" and selection.entry is not None:
            matches.append(
                _CatalogMatch(
                    binding=binding,
                    entry=selection.entry,
                    selection=selection,
                    revision=catalog.resolved_revision,
                )
            )

    ranked_specificities = [
        *(match.selection.specificity for match in matches),
        *(specificity for specificity, _candidates in ambiguous),
    ]
    best_specificity = max(ranked_specificities, default=(0, 0, 0))
    best_matches = [match for match in matches if match.selection.specificity == best_specificity]
    best_ambiguous = [
        candidate
        for specificity, candidates in ambiguous
        if specificity == best_specificity
        for candidate in candidates
    ]

    if best_ambiguous or len(best_matches) > 1:
        candidates = [
            *best_ambiguous,
            *(f"{match.binding.config.name}/{match.entry.document_id}" for match in best_matches),
        ]
        return _result(
            _STATUS_AMBIGUOUS,
            "More than one runbook is an equally valid exact match.",
            candidates=sorted(candidates),
            warnings=warnings,
        )
    if len(best_matches) == 1:
        match = best_matches[0]
        result = _fetch_document(
            match.binding,
            _catalog_reference(match),
            match=match,
        )
        result["warnings"] = [*warnings, *result["warnings"]]
        return result
    if manifest_sources and not readable_catalogs:
        return _result(
            _STATUS_UNAVAILABLE,
            "No configured runbook catalog could be retrieved.",
            available=False,
            warnings=warnings,
        )
    if not manifest_sources:
        warnings.append("No selected runbook source has a manifest configured.")
    return _result(
        _STATUS_NOT_FOUND,
        "No runbook exactly matched the incident identity.",
        warnings=warnings,
    )


_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "available": {"type": "boolean"},
        "status": {
            "type": "string",
            "enum": [
                _STATUS_LOADED,
                _STATUS_NOT_FOUND,
                _STATUS_AMBIGUOUS,
                _STATUS_UNAVAILABLE,
            ],
        },
        "message": {"type": "string"},
        "candidates": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "runbook": {
            "type": "object",
            "properties": {
                "source_name": {"type": "string"},
                "provider": {"type": "string"},
                "repository": {"type": "string"},
                "document_id": {"type": "string"},
                "title": {"type": "string"},
                "path": {"type": "string"},
                "revision": {"type": "string"},
                "url": {"type": "string"},
                "content": {"type": "string"},
                "truncated": {"type": "boolean"},
                "match_reason": {"type": "string"},
                "matched_fields": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "source_name",
                "provider",
                "repository",
                "document_id",
                "title",
                "path",
                "revision",
                "url",
                "content",
                "truncated",
                "match_reason",
                "matched_fields",
            ],
            "additionalProperties": False,
        },
    },
    "required": ["available", "status", "message", "candidates", "warnings"],
    "additionalProperties": False,
}


@tool(
    name="load_runbook_guidance",
    display_name="Runbook guidance",
    source="knowledge",
    description=(
        "Load an organization-owned runbook from a configured trusted source before or during "
        "an incident investigation. Prefer an explicit runbook URL from the user or alert; "
        "otherwise provide exact alert identity fields for deterministic manifest matching."
    ),
    use_cases=[
        "An incident or alert includes a runbook URL",
        "The user asks OpenSRE to follow an organization runbook",
        "An alertname, service, and labels can select a configured runbook manifest entry",
    ],
    anti_examples=[
        "General SRE advice when no organization-owned runbook is configured",
        "Searching arbitrary repositories or accepting untrusted document URLs",
    ],
    tags=("safe", "read-only", "runbook"),
    surfaces=(ToolSurface.CHAT,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    parallel_safe=True,
    accepts_runtime_context=True,
    is_available=_runbook_sources_available,
    input_schema={
        "type": "object",
        "properties": {
            "runbook_url": {
                "type": "string",
                "description": "Explicit trusted runbook URL supplied by the user or alert.",
            },
            "alertname": {
                "type": "string",
                "description": "Exact alert name used for manifest matching.",
            },
            "service": {
                "type": "string",
                "description": "Exact service name used for manifest matching.",
            },
            "labels": {
                "type": "object",
                "description": "Exact alert labels used for manifest matching.",
                "additionalProperties": {"type": "string"},
            },
            "source_name": {
                "type": "string",
                "description": "Optional configured source name used to narrow selection.",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
    output_schema=_OUTPUT_SCHEMA,
    evidence_mapper=map_runbook_guidance,
)
def load_runbook_guidance(
    runbook_url: str = "",
    alertname: str = "",
    service: str = "",
    labels: dict[str, str] | None = None,
    source_name: str = "",
    context: AgentToolContext | None = None,
) -> dict[str, Any]:
    """Load one trusted runbook using explicit URL or exact manifest matching."""
    bindings, warnings = _load_bindings(context, source_name.strip())
    if not bindings:
        return _result(
            _STATUS_UNAVAILABLE,
            "No configured runbook source is currently available.",
            available=False,
            warnings=warnings,
        )

    normalized_url = runbook_url.strip()
    if normalized_url:
        return _load_explicit_url(bindings, normalized_url, warnings)

    incident = IncidentIdentity.from_values(
        alertname=alertname,
        service=service,
        labels=labels,
    )
    if not incident.alertname and not incident.service:
        return _result(
            _STATUS_NOT_FOUND,
            "Provide a runbook URL or an exact alertname/service for runbook selection.",
            warnings=warnings,
        )
    return _load_catalog_match(bindings, incident, warnings)


__all__ = ["load_runbook_guidance"]
