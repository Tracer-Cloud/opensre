"""Prefect failed flow runs investigation tool."""

from __future__ import annotations

from typing import Any

from app.integrations.clients.prefect import make_prefect_client
from app.tools.base import BaseTool

_ERROR_KEYWORDS = ("error", "failed", "exception", "fatal", "crash", "traceback", "exitcode")
_FAILED_STATES = {"FAILED", "CRASHED", "CANCELLED", "CANCELLING"}


class PrefectFlowRunsTool(BaseTool):
    """Fetch and triage recent Prefect flow runs, surfacing failures for RCA."""

    name = "prefect_flow_runs"
    source = "prefect"
    description = (
        "Fetch recent Prefect flow runs filtered by state, and retrieve logs for failed runs "
        "to surface orchestration failures and root-cause evidence."
    )
    use_cases = [
        "Investigating why a Prefect flow run failed or crashed",
        "Listing all recent FAILED or CRASHED flow runs for triage",
        "Fetching logs from a specific failed flow run",
        "Correlating Prefect flow failures with infrastructure alerts",
        "Identifying recurring flow failures across deployments",
    ]
    requires = ["api_url"]
    input_schema = {
        "type": "object",
        "properties": {
            "api_url": {
                "type": "string",
                "description": (
                    "Prefect API base URL. Use https://api.prefect.cloud/api for Prefect Cloud "
                    "or your self-hosted server URL (e.g. http://localhost:4200/api)."
                ),
            },
            "api_key": {
                "type": "string",
                "default": "",
                "description": "Prefect Cloud API key. Leave empty for self-hosted servers with no auth.",
            },
            "account_id": {
                "type": "string",
                "default": "",
                "description": "Prefect Cloud account ID (required for Prefect Cloud).",
            },
            "workspace_id": {
                "type": "string",
                "default": "",
                "description": "Prefect Cloud workspace ID (required for Prefect Cloud).",
            },
            "states": {
                "type": "array",
                "items": {"type": "string"},
                "default": ["FAILED", "CRASHED"],
                "description": "Flow run states to filter on. Defaults to FAILED and CRASHED.",
            },
            "limit": {
                "type": "integer",
                "default": 20,
                "description": "Maximum number of flow runs to return.",
            },
            "fetch_logs_for_run_id": {
                "type": "string",
                "default": "",
                "description": (
                    "Optional flow run ID to fetch detailed logs for. "
                    "Use after identifying a specific failed run."
                ),
            },
            "log_limit": {
                "type": "integer",
                "default": 100,
                "description": "Maximum number of log lines to fetch per flow run.",
            },
        },
        "required": ["api_url"],
    }
    outputs = {
        "flow_runs": "List of matching flow runs with state and timing metadata",
        "failed_runs": "Subset of runs in FAILED or CRASHED state",
        "logs": "Log lines for the requested flow run (if fetch_logs_for_run_id is set)",
        "error_log_lines": "Log lines containing error keywords",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return bool(sources.get("prefect", {}).get("connection_verified"))

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        prefect = sources.get("prefect", {})
        return {
            "api_url": prefect.get("api_url", ""),
            "api_key": prefect.get("api_key", ""),
            "account_id": prefect.get("account_id", ""),
            "workspace_id": prefect.get("workspace_id", ""),
            "states": ["FAILED", "CRASHED"],
            "limit": 20,
            "fetch_logs_for_run_id": "",
            "log_limit": 100,
        }

    def run(
        self,
        api_url: str,
        api_key: str = "",
        account_id: str = "",
        workspace_id: str = "",
        states: list[str] | None = None,
        limit: int = 20,
        fetch_logs_for_run_id: str = "",
        log_limit: int = 100,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not (api_url or "").strip():
            return {
                "source": "prefect",
                "available": False,
                "error": "api_url is required to connect to Prefect.",
                "flow_runs": [],
                "failed_runs": [],
                "logs": [],
                "error_log_lines": [],
            }

        client = make_prefect_client(
            api_url=api_url,
            api_key=api_key,
            account_id=account_id,
            workspace_id=workspace_id,
        )
        if client is None:
            return {
                "source": "prefect",
                "available": False,
                "error": "Prefect integration could not be initialized. Check your api_url.",
                "flow_runs": [],
                "failed_runs": [],
                "logs": [],
                "error_log_lines": [],
            }

        effective_states = states if states is not None else ["FAILED", "CRASHED"]

        with client:
            runs_result = client.get_flow_runs(limit=limit, states=effective_states)
            if not runs_result.get("success"):
                return {
                    "source": "prefect",
                    "available": False,
                    "error": runs_result.get("error", "Unknown error fetching flow runs."),
                    "flow_runs": [],
                    "failed_runs": [],
                    "logs": [],
                    "error_log_lines": [],
                }

            flow_runs: list[dict[str, Any]] = runs_result.get("flow_runs", [])
            failed_runs = [
                r for r in flow_runs if r.get("state_type", "").upper() in _FAILED_STATES
            ]

            logs: list[dict[str, Any]] = []
            error_log_lines: list[dict[str, Any]] = []

            logs_error: str | None = None
            if fetch_logs_for_run_id:
                logs_result = client.get_flow_run_logs(
                    flow_run_id=fetch_logs_for_run_id, limit=log_limit
                )
                if logs_result.get("success"):
                    logs = logs_result.get("logs", [])
                    error_log_lines = [
                        line
                        for line in logs
                        if any(kw in line.get("message", "").lower() for kw in _ERROR_KEYWORDS)
                    ]
                else:
                    logs_error = logs_result.get("error", "Unknown error fetching logs.")

        result: dict[str, Any] = {
            "source": "prefect",
            "available": True,
            "flow_runs": flow_runs,
            "total": len(flow_runs),
            "failed_runs": failed_runs,
            "total_failed": len(failed_runs),
            "logs": logs,
            "error_log_lines": error_log_lines,
            "fetched_logs_for_run_id": fetch_logs_for_run_id or None,
        }
        if logs_error is not None:
            result["logs_error"] = logs_error
        return result


prefect_flow_runs = PrefectFlowRunsTool()
