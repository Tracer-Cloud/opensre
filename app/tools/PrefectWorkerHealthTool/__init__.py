"""Prefect worker and work pool health investigation tool."""

from __future__ import annotations

from typing import Any

from app.integrations.clients.prefect import make_prefect_client
from app.tools.base import BaseTool

_UNHEALTHY_WORKER_STATUSES = {"OFFLINE", "UNHEALTHY"}
_UNHEALTHY_POOL_STATUSES = {"NOT_READY", "PAUSED"}


class PrefectWorkerHealthTool(BaseTool):
    """Inspect Prefect work pool and worker health to identify orchestration bottlenecks."""

    name = "prefect_worker_health"
    source = "prefect"
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
            return {
                "source": "prefect",
                "available": False,
                "error": "api_url is required to connect to Prefect.",
                "work_pools": [],
                "unhealthy_pools": [],
                "workers": [],
                "unhealthy_workers": [],
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
                "work_pools": [],
                "unhealthy_pools": [],
                "workers": [],
                "unhealthy_workers": [],
            }

        with client:
            pools_result = client.get_work_pools(limit=pool_limit)
            if not pools_result.get("success"):
                return {
                    "source": "prefect",
                    "available": False,
                    "error": pools_result.get("error", "Unknown error fetching work pools."),
                    "work_pools": [],
                    "unhealthy_pools": [],
                    "workers": [],
                    "unhealthy_workers": [],
                }

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
