"""``query_new_relic_metrics`` — arbitrary NRQL metrics evidence (FR-7)."""

from __future__ import annotations

from typing import Any

from config.constants.new_relic import (
    NEW_RELIC_DEFAULT_INCIDENT_LIMIT,
    NEW_RELIC_DEFAULT_WINDOW_MINUTES,
)
from core.domain.types.evidence import record_evidence_entry
from core.tool import BaseTool, EvidenceType, SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.new_relic.client import NewRelicClient
from integrations.new_relic.config import NewRelicIntegrationConfig
from integrations.new_relic.tools.new_relic_metrics_tool.validation import (
    apply_default_window_and_limit,
    clamp_timeseries_buckets,
    extract_limit,
    validate_nrql,
)

_SOURCE = "new_relic"

_USE_CASES = [
    "Running the exact nrqlQuery returned by query_new_relic_alerts to re-check a metric",
    "Querying a custom NRQL metric for infrastructure or application performance data",
]
_ANTI_EXAMPLES = [
    "Use query_new_relic_alerts for incident/condition/policy evidence, not this tool.",
    "Do not pass a GraphQL mutation string as nrql — this tool is read-only.",
]


def _new_relic_source(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    source = sources.get("new_relic")
    return source if isinstance(source, dict) else {}


def _is_available(sources: dict[str, dict[str, Any]]) -> bool:
    new_relic = _new_relic_source(sources)
    return bool(new_relic.get("connection_verified") and new_relic.get("account_id"))


def _extract_params(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    new_relic = _new_relic_source(sources)
    return {
        "api_key": new_relic.get("api_key"),
        "account_id": new_relic.get("account_id"),
        "base_url": new_relic.get("base_url"),
    }


def _map_query_new_relic_metrics(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the returned row count, noting when the fetch was truncated."""
    if not output.get("available"):
        return
    results = output.get("results") or []
    if not results:
        return
    summary = f"{len(results)} row(s)"
    if output.get("truncated"):
        summary += " (truncated)"
    record_evidence_entry(
        evidence,
        source="query_new_relic_metrics",
        label="New Relic Metrics",
        summary=summary,
    )


class NewRelicMetricsTool(BaseTool):
    """Run a model-supplied NRQL query through NerdGraph and return raw rows."""

    name = "query_new_relic_metrics"
    source = _SOURCE
    evidence_mapper = _map_query_new_relic_metrics
    description = (
        "Run an NRQL SELECT query against the configured New Relic account and return "
        "normalized metric rows. Accepts the nrqlQuery field returned by "
        "query_new_relic_alerts as-is — re-running an alert's own query is the main "
        "bridge between the two tools. A default SINCE/LIMIT is injected when the "
        "query omits them, and a TIMESERIES bucket too fine for the window (NRQL "
        "allows at most 366 buckets) is widened."
    )
    use_cases = _USE_CASES
    anti_examples = _ANTI_EXAMPLES
    requires = ["new_relic"]
    evidence_type = EvidenceType.METRICS
    side_effect_level = SideEffectLevel.READ_ONLY
    injected_params = ("api_key", "account_id", "base_url")
    input_schema = {
        "type": "object",
        "properties": {
            "nrql": {
                "type": "string",
                "description": (
                    "NRQL SELECT query, e.g. the nrqlQuery field returned by "
                    "query_new_relic_alerts. NRQL syntax notes: use count(*) "
                    "(never count()); backtick-quote dotted attribute names "
                    "(`error.class`, `http.statusCode`) wherever they appear; keep "
                    "parentheses balanced; prefer TIMESERIES AUTO or a bucket "
                    "giving at most 366 buckets over the SINCE window."
                ),
            },
            "api_key": {"type": "string"},
            "account_id": {"type": "string"},
            "base_url": {"type": "string"},
        },
        "required": ["nrql"],
    }
    outputs = {
        "results": "raw NRQL result rows",
        "total": "number of rows returned",
        "truncated": "true when the row count hit the effective LIMIT (NFR-4)",
        "nrql": "the NRQL actually executed, after default injection",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        return _extract_params(sources)

    def run(
        self,
        *,
        nrql: str,
        api_key: str | None = None,
        account_id: str | None = None,
        base_url: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        is_valid, validation_error = validate_nrql(nrql)
        if not is_valid:
            return tool_unavailable(
                _SOURCE,
                validation_error,
                error_type="validation_error",
                results=[],
                total=0,
                truncated=False,
                nrql=nrql,
            )

        config = NewRelicIntegrationConfig(
            api_key=api_key or "",
            account_id=account_id or "",
            base_url=base_url or "",
        )
        client = NewRelicClient(config)
        if not client.is_configured:
            return tool_unavailable(
                _SOURCE,
                "New Relic integration is not configured.",
                results=[],
                total=0,
                truncated=False,
                nrql=nrql,
            )

        effective_nrql = clamp_timeseries_buckets(
            apply_default_window_and_limit(
                nrql,
                since_minutes=NEW_RELIC_DEFAULT_WINDOW_MINUTES,
                limit=NEW_RELIC_DEFAULT_INCIDENT_LIMIT,
            )
        )
        result = client.query_metrics(nrql=effective_nrql)
        if not result.get("success"):
            return tool_unavailable(
                _SOURCE,
                str(result.get("error", "Unknown New Relic error.")),
                error_type=result.get("error_type"),
                results=[],
                total=0,
                truncated=False,
                nrql=effective_nrql,
            )

        rows = result.get("results", [])
        effective_limit = extract_limit(effective_nrql)
        truncated = bool(effective_limit and len(rows) >= effective_limit)
        return {
            "source": _SOURCE,
            "available": True,
            "results": rows,
            "total": len(rows),
            "truncated": truncated,
            "nrql": effective_nrql,
        }


query_new_relic_metrics = tool(NewRelicMetricsTool())
