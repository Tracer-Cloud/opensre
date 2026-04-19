from typing import Any

from app.integrations.models import VictoriaLogsIntegrationConfig
from app.services.victoria_logs.client import VictoriaLogsClient
from app.tools.base import BaseTool


class VictoriaLogsTool(BaseTool):
    name = "victoria_logs_query"
    source = "victoria_logs"
    description = "Query structured logs from VictoriaLogs using LogsQL to investigate application errors or anomalies."
    use_cases = ["Investigating logs", "Analyzing specific log streams"]
    requires = ["base_url"]
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "LogsQL query to filter logs. Example: `_stream_id:* AND error`",
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of logs to retrieve.",
            },
            "start": {
                "type": "string",
                "default": "-1h",
                "description": "Time range, e.g., -1h or -24h.",
            },
        },
        "required": ["query"],
    }
    outputs = {"logs": "List of structured log entries matching the LogsQL query."}

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("victoria_logs", {}).get("connection_verified"))

    def extract_params(self, _sources: dict) -> dict[str, Any]:
        return {}

    def run(
        self, query: str, limit: int = 50, start: str = "-1h", **kwargs: Any
    ) -> dict[str, Any]:
        vl_conf = kwargs.get("sources", {}).get("victoria_logs", {})
        config = VictoriaLogsIntegrationConfig(
            base_url=vl_conf.get("base_url", ""),
            tenant_id=vl_conf.get("tenant_id", "0"),
        )

        if not config.base_url:
            return {
                "source": "victoria_logs",
                "available": False,
                "error": "VictoriaLogs base_url is required.",
            }

        client = VictoriaLogsClient(config)
        result = client.query_logs(query=query, limit=limit, start=start)

        if not result.get("success"):
            return {
                "source": "victoria_logs",
                "available": False,
                "error": result.get("error", "unknown error"),
                "logs": [],
            }

        return {
            "source": "victoria_logs",
            "available": True,
            "logs": result.get("rows", []),
            "query": query,
        }


victoria_logs_query = VictoriaLogsTool()
