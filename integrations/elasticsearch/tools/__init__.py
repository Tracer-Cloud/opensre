# ======== from tools/elasticsearch_logs_tool/ ========

"""Elasticsearch log search tool."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.tool import BaseTool
from infrastructure.evidence.evidence_compaction import compact_logs, summarize_counts
from integrations.elasticsearch._client import make_client
from integrations.elasticsearch.search_failures import not_configured, search_failed

_ERROR_KEYWORDS = (
    "error",
    "fail",
    "exception",
    "traceback",
    "critical",
    "killed",
    "crash",
    "panic",
    "timeout",
)


def _map_elasticsearch_logs(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    logs = output.get("logs", [])
    error_logs = output.get("error_logs", [])
    evidence["elasticsearch_logs"] = logs
    evidence["elasticsearch_error_logs"] = error_logs
    evidence["elasticsearch_logs_query"] = output.get("query") or tool_input.get("query", "")
    if logs:
        summary_parts = [f"{len(logs)} logs"]
        if error_logs:
            summary_parts.append(f"{len(error_logs)} errors")
        record_evidence_entry(
            evidence,
            source="query_elasticsearch_logs",
            label="Elasticsearch Logs",
            summary=", ".join(summary_parts),
        )


class ElasticsearchLogsTool(BaseTool):
    """Search Elasticsearch logs for errors, exceptions, and application events."""

    name = "query_elasticsearch_logs"
    evidence_mapper = _map_elasticsearch_logs
    source = "elasticsearch"
    description = "Search Elasticsearch logs for errors, exceptions, and application events."
    use_cases = [
        "Investigating application errors stored in Elasticsearch",
        "Searching logs across multiple indices or data streams",
        "Filtering logs by time range and query string",
        "Inspecting cluster health and available indices",
    ]
    requires = []
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Lucene/KQL query string (default: *)"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 50},
            "index_pattern": {
                "type": "string",
                "description": "Index pattern to search (e.g. 'logs-*'). Defaults to the configured OpenSearch/Elasticsearch index_pattern or '*'.",
            },
            "url": {
                "type": "string",
                "description": "Elasticsearch/OpenSearch URL (overrides the configured OPENSEARCH_URL)",
            },
            "api_key": {
                "type": "string",
                "description": "API key for authenticated clusters (optional)",
            },
            "username": {
                "type": "string",
                "description": "Username for HTTP Basic Auth (optional, used when api_key is not provided)",
            },
            "password": {
                "type": "string",
                "description": "Password for HTTP Basic Auth (optional, used when api_key is not provided)",
            },
        },
        "required": ["query"],
    }

    def is_available(self, sources: dict) -> bool:
        # Shares the "opensearch" source: same client, same credentials (see
        # docs/integrations/databases/opensearch.mdx — configuring OpenSearch/Elasticsearch once
        # enables both the analytics tool and this log-search tool).
        return bool(sources.get("opensearch", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict:
        es = sources.get("opensearch", {})
        return {
            "query": es.get("default_query", "*"),
            "time_range_minutes": es.get("time_range_minutes", 60),
            "limit": 50,
            "url": es.get("url"),
            "api_key": es.get("api_key"),
            "username": es.get("username"),
            "password": es.get("password"),
            "index_pattern": es.get("index_pattern", "*"),
        }

    def run(
        self,
        query: str = "*",
        time_range_minutes: int = 60,
        limit: int = 50,
        index_pattern: str = "*",
        url: str | None = None,
        api_key: str | None = None,
        username: str | None = None,
        password: str | None = None,
        **_kwargs: Any,
    ) -> dict:
        client = make_client(
            url,
            api_key=api_key,
            username=username,
            password=password,
            index_pattern=index_pattern,
        )
        if not client:
            return not_configured("elasticsearch_logs", vendor="Elasticsearch")

        result = client.search_logs(
            query=query,
            time_range_minutes=time_range_minutes,
            limit=limit,
        )
        if not result.get("success"):
            return search_failed("elasticsearch_logs", result, vendor="Elasticsearch")

        logs = result.get("logs", [])
        error_logs = [
            log
            for log in logs
            if any(kw in log.get("message", "").lower() for kw in _ERROR_KEYWORDS)
        ]

        # Compact logs to stay within prompt limits
        compacted_logs = compact_logs(logs, limit=50)
        compacted_error_logs = compact_logs(error_logs, limit=30)

        result_data = {
            "source": "elasticsearch_logs",
            "available": True,
            "logs": compacted_logs,
            "error_logs": compacted_error_logs,
            "total": result.get("total", 0),
            "query": query,
        }
        summary = summarize_counts(result.get("total", 0), len(compacted_logs), "logs")
        if summary:
            result_data["truncation_note"] = summary
        return result_data


# Module-level alias for direct invocation
query_elasticsearch_logs = ElasticsearchLogsTool()
