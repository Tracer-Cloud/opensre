# ======== from tools/prefect_flow_runs_tool/ ========

"""Prefect failed flow runs investigation tool."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework.utils import tool_unavailable
from infrastructure.text.truncation import truncate
from integrations.prefect.client import make_prefect_client

_ERROR_KEYWORDS = ("error", "failed", "exception", "fatal", "crash", "traceback", "exitcode")
_FAILED_STATES = {"FAILED", "CRASHED", "CANCELLED", "CANCELLING"}

#: Bound the caller-supplied work pool name echoed into a report summary.
_POOL_NAME_SUMMARY_TRUNCATE_LEN = 60

#: Prefect's ``/flow_runs/filter``, ``/work_pools/filter``, and
#: ``/work_pools/{name}/workers/filter`` endpoints all cap ``limit`` at 200
#: server-side (``min(limit, 200)`` in ``integrations/prefect/client.py``),
#: and none surfaces pagination metadata -- a returned count cannot be
#: distinguished from a true total except by comparing it against the
#: effective page size that was requested.
_PREFECT_MAX_PAGE_SIZE = 200


def _prefect_page_is_truncated(returned_count: int, requested_limit: int) -> bool:
    effective_limit = min(max(requested_limit, 1), _PREFECT_MAX_PAGE_SIZE)
    return returned_count >= effective_limit


def _map_prefect_flow_runs(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the flow run count, failed count, and error-log-line count when fetched."""
    if not output.get("available"):
        return
    flow_runs = output.get("flow_runs") or []
    if not flow_runs:
        return
    total = output.get("total", len(flow_runs))
    truncated = _prefect_page_is_truncated(total, tool_input.get("limit", 20))
    total_label = f"{total}+" if truncated else str(total)
    failed_count = output.get("total_failed", 0)
    # A truncated page's failed-count is only a floor even when it's zero --
    # zero failed runs *in the returned page* does not mean zero overall.
    failed_label = f"{failed_count}+" if truncated else str(failed_count)
    parts = [f"{total_label} flow run(s), {failed_label} failed"]
    if output.get("fetched_logs_for_run_id"):
        error_count = len(output.get("error_log_lines") or [])
        parts.append(f"{error_count} error log line(s)")
    record_evidence_entry(
        evidence,
        source="prefect_flow_runs",
        label="Prefect Flow Runs",
        summary=", ".join(parts),
    )


class PrefectFlowRunsTool(BaseTool):
    """Fetch and triage recent Prefect flow runs, surfacing failures for RCA."""

    name = "prefect_flow_runs"
    source = "prefect"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    evidence_mapper = _map_prefect_flow_runs
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
    injected_params = ["api_key", "api_url", "workspace_id"]
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
            return tool_unavailable(
                "prefect",
                "api_url is required to connect to Prefect.",
                flow_runs=[],
                failed_runs=[],
                logs=[],
                error_log_lines=[],
            )

        client = make_prefect_client(
            api_url=api_url,
            api_key=api_key,
            account_id=account_id,
            workspace_id=workspace_id,
        )
        if client is None:
            return tool_unavailable(
                "prefect",
                "Prefect integration could not be initialized. Check your api_url.",
                flow_runs=[],
                failed_runs=[],
                logs=[],
                error_log_lines=[],
            )

        effective_states = states if states is not None else ["FAILED", "CRASHED"]

        with client:
            runs_result = client.get_flow_runs(limit=limit, states=effective_states)
            if not runs_result.get("success"):
                return tool_unavailable(
                    "prefect",
                    runs_result.get("error", "Unknown error fetching flow runs."),
                    flow_runs=[],
                    failed_runs=[],
                    logs=[],
                    error_log_lines=[],
                )

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


# ======== from tools/prefect_worker_health_tool/ ========

"""Prefect worker and work pool health investigation tool."""


from core.tool import BaseTool

_UNHEALTHY_WORKER_STATUSES = {"OFFLINE", "UNHEALTHY"}
_UNHEALTHY_POOL_STATUSES = {"NOT_READY", "PAUSED"}


def _map_prefect_worker_health(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite work pool health, and worker health when a specific pool was inspected."""
    if not output.get("available"):
        return
    work_pools = output.get("work_pools") or []
    if not work_pools:
        return
    total_pools = output.get("total_pools", len(work_pools))
    unhealthy_pools = output.get("total_unhealthy_pools", 0)
    parts = [f"{total_pools} work pool(s), {unhealthy_pools} unhealthy"]
    pool_name = output.get("work_pool_name")
    if pool_name:
        safe_pool_name = truncate(
            str(pool_name).replace("\n", " "), _POOL_NAME_SUMMARY_TRUNCATE_LEN
        )
        total_workers = output.get("total_workers", 0)
        unhealthy_workers = output.get("total_unhealthy_workers", 0)
        parts.append(
            f"{total_workers} worker(s) in '{safe_pool_name}', {unhealthy_workers} unhealthy"
        )
    record_evidence_entry(
        evidence,
        source="prefect_worker_health",
        label="Prefect Worker Health",
        summary=", ".join(parts),
    )


class PrefectWorkerHealthTool(BaseTool):
    """Inspect Prefect work pool and worker health to identify orchestration bottlenecks."""

    name = "prefect_worker_health"
    source = "prefect"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    evidence_mapper = _map_prefect_worker_health
    description = (
        "Inspect Prefect work pools and their registered workers to identify offline, "
        "unhealthy, or paused workers that may be blocking flow run execution."
    )
    use_cases = [
        "Diagnosing why Prefect flows are stuck in PENDING state",
        "Identifying offline or unresponsive Prefect workers",
        "Checking which work pools are paused or have no active workers",
        "Investigating worker heartbeat failures",
        "Auditing work pool concurrency limits during incident investigation",
    ]
    requires = ["api_url"]
    injected_params = ["api_key", "api_url", "workspace_id"]
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
            "work_pool_name": {
                "type": "string",
                "default": "",
                "description": (
                    "Name of a specific work pool to inspect workers for. "
                    "If omitted, lists all work pools without drilling into workers."
                ),
            },
            "pool_limit": {
                "type": "integer",
                "default": 20,
                "description": "Maximum number of work pools to list.",
            },
            "worker_limit": {
                "type": "integer",
                "default": 20,
                "description": "Maximum number of workers to list per work pool.",
            },
        },
        "required": ["api_url"],
    }
    outputs = {
        "work_pools": "All listed work pools with status and pause state",
        "unhealthy_pools": "Work pools that are paused or in NOT_READY state",
        "workers": "Workers registered in the requested work pool",
        "unhealthy_workers": "Workers that are OFFLINE or UNHEALTHY",
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
            "work_pool_name": prefect.get("work_pool_name", ""),
            "pool_limit": 20,
            "worker_limit": 20,
        }

    def run(
        self,
        api_url: str,
        api_key: str = "",
        account_id: str = "",
        workspace_id: str = "",
        work_pool_name: str = "",
        pool_limit: int = 20,
        worker_limit: int = 20,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not (api_url or "").strip():
            return tool_unavailable(
                "prefect",
                "api_url is required to connect to Prefect.",
                work_pools=[],
                unhealthy_pools=[],
                workers=[],
                unhealthy_workers=[],
            )

        client = make_prefect_client(
            api_url=api_url,
            api_key=api_key,
            account_id=account_id,
            workspace_id=workspace_id,
        )
        if client is None:
            return tool_unavailable(
                "prefect",
                "Prefect integration could not be initialized. Check your api_url.",
                work_pools=[],
                unhealthy_pools=[],
                workers=[],
                unhealthy_workers=[],
            )

        with client:
            pools_result = client.get_work_pools(limit=pool_limit)
            if not pools_result.get("success"):
                return tool_unavailable(
                    "prefect",
                    pools_result.get("error", "Unknown error fetching work pools."),
                    work_pools=[],
                    unhealthy_pools=[],
                    workers=[],
                    unhealthy_workers=[],
                )

            work_pools: list[dict[str, Any]] = pools_result.get("work_pools", [])
            unhealthy_pools = [
                p
                for p in work_pools
                if p.get("status", "").upper() in _UNHEALTHY_POOL_STATUSES
                or p.get("is_paused", False)
            ]

            workers: list[dict[str, Any]] = []
            unhealthy_workers: list[dict[str, Any]] = []

            if work_pool_name:
                workers_result = client.get_workers(
                    work_pool_name=work_pool_name, limit=worker_limit
                )
                if workers_result.get("success"):
                    workers = workers_result.get("workers", [])
                    unhealthy_workers = [
                        w
                        for w in workers
                        if w.get("status", "").upper() in _UNHEALTHY_WORKER_STATUSES
                    ]

        return {
            "source": "prefect",
            "available": True,
            "work_pools": work_pools,
            "total_pools": len(work_pools),
            "unhealthy_pools": unhealthy_pools,
            "total_unhealthy_pools": len(unhealthy_pools),
            "work_pool_name": work_pool_name or None,
            "workers": workers,
            "total_workers": len(workers),
            "unhealthy_workers": unhealthy_workers,
            "total_unhealthy_workers": len(unhealthy_workers),
        }


prefect_worker_health = PrefectWorkerHealthTool()
