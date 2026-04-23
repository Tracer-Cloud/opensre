"""Splunk log search tool for RCA investigation."""

from __future__ import annotations

from typing import Any

from app.services.splunk import SplunkClient, SplunkConfig
from app.tools.base import BaseTool
from app.tools.utils.compaction import compact_logs, summarize_counts

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


class SplunkSearchTool(BaseTool):
    """Search Splunk logs using SPL for errors, exceptions, and application events."""

    name = "query_splunk_logs"
    source = "splunk"
    description = (
        "Search Splunk using SPL (Search Processing Language) for application errors, "
        "exceptions, and operational events. Returns time-bounded log evidence."
    )
    use_cases = [
        "Investigating application errors stored in Splunk",
        "Searching Splunk indexes for error patterns during incident window",
        "Fetching recent error logs for a service identified in an alert",
        "Correlating trace IDs with Splunk log entries",
    ]
    requires = []   # connection_verified check is in is_available()
    outputs = {
        "splunk_logs": "All log events returned from Splunk search",
        "splunk_error_logs": "Subset of logs matching error/exception keywords",
    }
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "SPL search string (e.g. 'index=main error | head 50'). "
                    "Do not include the leading 'search' keyword."
                ),
            },
            "time_range_minutes": {
                "type": "integer",
                "default": 60,
                "description": "Look-back window in minutes from now.",
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of events to return.",
            },
            "index": {
                "type": "string",
                "description": "Splunk index to search (overrides integration default).",
            },
        },
        "required": ["query"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("splunk", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict:
        splunk = sources.get("splunk", {})
        return {
            "query": splunk.get("default_query", f"index={splunk.get('index', 'main')} | head 50"),
            "time_range_minutes": splunk.get("time_range_minutes", 60),
            "limit": 50,
            "index": splunk.get("index", "main"),
            "base_url": splunk.get("base_url"),
            "token": splunk.get("token"),
            "verify_ssl": splunk.get("verify_ssl", True),
        }

    def run(
        self,
        query: str,
        time_range_minutes: int = 60,
        limit: int = 50,
        index: str = "main",
        base_url: str | None = None,
        token: str | None = None,
        verify_ssl: bool = True,
        **_kwargs: Any,
    ) -> dict:
        if not base_url or not token:
            return {
                "source": "splunk_logs",
                "available": False,
                "error": "Splunk integration not configured (missing base_url or token)",
                "logs": [],
            }

        config = SplunkConfig(base_url=base_url, token=token, index=index, verify_ssl=verify_ssl)
        client = SplunkClient(config)

        result = client.search_logs(
            query=query,
            time_range_minutes=time_range_minutes,
            limit=limit,
        )

        if not result.get("success"):
            return {
                "source": "splunk_logs",
                "available": False,
                "error": result.get("error", "Unknown error"),
                "logs": [],
            }

        logs = result.get("logs", [])
        error_logs = [
            log
            for log in logs
            if any(kw in log.get("message", "").lower() for kw in _ERROR_KEYWORDS)
        ]

        compacted_logs = compact_logs(logs, limit=50)
        compacted_error_logs = compact_logs(error_logs, limit=30)

        result_data: dict[str, Any] = {
            "source": "splunk_logs",
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
query_splunk_logs = SplunkSearchTool()
