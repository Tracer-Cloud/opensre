"""Grafana alert rules query tool."""

from __future__ import annotations

from typing import Any

import integrations.grafana.tools._helpers as grafana_helpers
from core.domain.types.evidence import record_evidence_entry
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable

_GRAFANA_RUNTIME_PARAMS = grafana_helpers.GRAFANA_RUNTIME_PARAMS


def _query_grafana_alert_rules_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = grafana_helpers._grafana_source(sources)
    return {
        "folder": grafana.get("pipeline_name"),
        "grafana_backend": grafana.get("_backend"),
        **grafana_helpers._grafana_creds(grafana),
    }


def _query_grafana_alert_rules_available(sources: dict[str, dict]) -> bool:
    return grafana_helpers._grafana_available(sources)


def _normalize_backend_alert_rules(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize fixture/backend ruler responses to the client rule shape."""
    rules: list[dict[str, Any]] = []
    for group in raw.get("groups", []):
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("name", ""))
        folder = str(group.get("folder", ""))
        for rule in group.get("rules", []):
            if not isinstance(rule, dict):
                continue
            annotations = rule.get("annotations", {})
            labels = rule.get("labels", {})
            rules.append(
                {
                    "rule_name": rule.get("name") or rule.get("title") or "unknown",
                    "state": rule.get("state", ""),
                    "folder": folder,
                    "group": group_name,
                    "queries": rule.get("queries", []),
                    "labels": labels if isinstance(labels, dict) else {},
                    "annotations": annotations if isinstance(annotations, dict) else {},
                    "no_data_state": rule.get("no_data_state") or rule.get("noDataState"),
                }
            )
    return rules


def _map_grafana_alert_rules(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    rules = output.get("rules", [])
    evidence["grafana_alert_rules"] = rules
    if rules:
        record_evidence_entry(
            evidence,
            source="grafana_alert_rules",
            label="Grafana Alert Rules",
            summary=f"{len(rules)} rules",
        )


@tool(
    name="query_grafana_alert_rules",
    display_name="Grafana alerts",
    source="grafana",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    evidence_mapper=_map_grafana_alert_rules,
    description="Query Grafana alert rules to understand what is being monitored.",
    use_cases=[
        "Investigating DatasourceNoData alerts to find the exact PromQL/LogQL query",
        "Understanding monitoring configuration and thresholds",
        "Auditing which alerts are active for a pipeline",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "folder": {"type": "string"},
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
        },
        "required": [],
    },
    injected_params=_GRAFANA_RUNTIME_PARAMS,
    is_available=_query_grafana_alert_rules_available,
    extract_params=_query_grafana_alert_rules_extract_params,
)
def query_grafana_alert_rules(
    folder: str | None = None,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_username: str = "",
    grafana_password: str = "",
    grafana_verify_ssl: bool = True,
    grafana_ca_bundle: str = "",
    grafana_backend: Any = None,
    **_kwargs: Any,
) -> dict:
    """Query Grafana alert rules to understand what is being monitored."""
    if grafana_backend is not None:
        raw = grafana_backend.query_alert_rules()
        rules = _normalize_backend_alert_rules(raw)
        return {
            "source": "grafana_alerts",
            "available": True,
            "rules": rules,
            "total_rules": len(rules),
            "raw": raw,
        }

    client = grafana_helpers._resolve_grafana_client(
        grafana_endpoint,
        grafana_api_key,
        grafana_username,
        grafana_password,
        grafana_verify_ssl,
        grafana_ca_bundle,
    )
    if not client or not client.is_configured:
        return tool_unavailable("grafana_alerts", "Grafana integration not configured", rules=[])

    rules = client.query_alert_rules(folder=folder)
    return {
        "source": "grafana_alerts",
        "available": True,
        "rules": rules,
        "total_rules": len(rules),
        "folder_filter": folder,
    }


__all__ = ["query_grafana_alert_rules"]
