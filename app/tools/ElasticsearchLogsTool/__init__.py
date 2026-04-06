"""Elasticsearch log search tool."""

from __future__ import annotations

from typing import Any

from app.tools.base import BaseTool
from app.tools.ElasticsearchLogsTool._client import make_client, unavailable

_ERROR_KEYWORDS = (
    "error", "fail", "exception", "traceback",
    "critical", "killed", "crash", "panic", "timeout",
)


class ElasticsearchLogsTool(BaseTool):
    """Search Elasticsearch logs for errors, exceptions, and application events."""

    name = "query_elasticsearch_logs"
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
                "description": "Index pattern to search (e.g. 'logs-*'). Defaults to ELASTICSEARCH_INDEX_PATTERN env var or '*'.",
            },
            "url": {"type": "string", "description": "Elasticsearch URL (overrides ELASTICSEARCH_URL env var)"},
            "api_key": {"type": "string", "description": "API key for authenticated clusters (optional)"},
        },
        "required": ["query"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("elasticsearch", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict:
        es = sources.get("elasticsearch", {})
        return {
            "query": es.get("default_query", "*"),
            "time_range_minutes": es.get("time_range_minutes", 60),
            "limit": 50,
            "url": es.get("url"),
            "api_key": es.get("api_key"),
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
        **_kwargs: Any,
    ) -> dict:
        client = make_client(url, api_key=api_key, index_pattern=index_pattern)
        if not client:
            return unavailable("elasticsearch_logs", "logs", "Elasticsearch integration not configured")

        result = client.search_logs(
            query=query,
            time_range_minutes=time_range_minutes,
            limit=limit,
        )
        if not result.get("success"):
            return unavailable("elasticsearch_logs", "logs", result.get("error", "Unknown error"))

        logs = result.get("logs", [])
        error_logs = [
            log for log in logs
            if any(kw in log.get("message", "").lower() for kw in _ERROR_KEYWORDS)
        ]
        return {
            "source": "elasticsearch_logs",
            "available": True,
            "logs": logs[:50],
            "error_logs": error_logs[:30],
            "total": result.get("total", 0),
            "query": query,
        }


# Module-level alias for direct invocation
query_elasticsearch_logs = ElasticsearchLogsTool()
