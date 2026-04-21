"""Prefect REST API client.

Wraps the Prefect Server / Prefect Cloud API endpoints used for flow run,
worker, and deployment investigation.
Credentials come from the user's Prefect integration stored locally or via env vars.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import field_validator

from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30
_PREFECT_CLOUD_BASE = "https://api.prefect.cloud/api"


class PrefectConfig(StrictConfigModel):
    """Normalized Prefect credentials.

    Supports both Prefect Cloud (api_key + account_id + workspace_id)
    and self-hosted Prefect Server (api_url only).
    """

    api_url: str = _PREFECT_CLOUD_BASE
    api_key: str = ""
    account_id: str = ""
    workspace_id: str = ""
    integration_id: str = ""

    @field_validator("api_url", mode="before")
    @classmethod
    def _normalize_api_url(cls, value: object) -> str:
        return str(value or _PREFECT_CLOUD_BASE).strip().rstrip("/")

    @field_validator("api_key", "account_id", "workspace_id", mode="before")
    @classmethod
    def _normalize_str(cls, value: object) -> str:
        return str(value or "").strip()

    @property
    def base_url(self) -> str:
        """Resolve the effective API base URL.

        For Prefect Cloud, the path includes account and workspace slugs.
        For self-hosted, api_url is used as-is.
        """
        if self.account_id and self.workspace_id:
            return f"{self.api_url}/accounts/{self.account_id}/workspaces/{self.workspace_id}"
        return self.api_url

    @property
    def headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h


class PrefectClient:
    """Synchronous client for the Prefect REST API."""

    def __init__(self, config: PrefectConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                headers=self.config.headers,
                timeout=_DEFAULT_TIMEOUT,
            )
        return self._client

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> PrefectClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def is_configured(self) -> bool:
        """Return True when an API key is set or a non-default API URL has been supplied."""
        return bool(self.config.api_key) or self.config.api_url != _PREFECT_CLOUD_BASE

    # ------------------------------------------------------------------
    # Flow runs
    # ------------------------------------------------------------------

    def get_flow_runs(
        self,
        limit: int = 20,
        states: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch recent flow runs, optionally filtered by state.

        Args:
            limit: Maximum number of flow runs to return.
            states: List of Prefect state names to filter on (e.g. ["FAILED", "CRASHED"]).
        """
        body: dict[str, Any] = {
            "limit": min(limit, 200),
            "sort": "START_TIME_DESC",
        }
        if states:
            body["flow_runs"] = {"state": {"type": {"any_": [s.upper() for s in states]}}}

        try:
            resp = self._get_client().post("/flow_runs/filter", json=body)
            resp.raise_for_status()
            raw: list[dict[str, Any]] = resp.json()
            runs = [
                {
                    "id": r.get("id", ""),
                    "name": r.get("name", ""),
                    "flow_id": r.get("flow_id", ""),
                    "state_type": r.get("state_type", ""),
                    "state_name": r.get("state_name", ""),
                    "start_time": r.get("start_time", ""),
                    "end_time": r.get("end_time", ""),
                    "duration": r.get("total_run_time", None),
                    "deployment_id": r.get("deployment_id", ""),
                    "tags": r.get("tags", []),
                }
                for r in raw
            ]
            return {"success": True, "flow_runs": runs, "total": len(runs)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[prefect] get_flow_runs HTTP failure status=%s",
                e.response.status_code,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning("[prefect] get_flow_runs error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}

    def get_flow_run_logs(self, flow_run_id: str, limit: int = 100) -> dict[str, Any]:
        """Fetch logs emitted during a specific flow run.

        Args:
            flow_run_id: The UUID of the flow run.
            limit: Maximum number of log lines to return.
        """
        body: dict[str, Any] = {
            "limit": min(limit, 1000),
            "logs": {"flow_run_id": {"any_": [flow_run_id]}},
            "sort": "TIMESTAMP_ASC",
        }
        try:
            resp = self._get_client().post("/logs/filter", json=body)
            resp.raise_for_status()
            raw: list[dict[str, Any]] = resp.json()
            logs = [
                {
                    "timestamp": entry.get("timestamp", ""),
                    "level": entry.get("level", ""),
                    "message": entry.get("message", ""),
                    "task_run_id": entry.get("task_run_id", ""),
                }
                for entry in raw
            ]
            return {"success": True, "logs": logs, "total": len(logs)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[prefect] get_flow_run_logs HTTP failure status=%s flow_run_id=%r",
                e.response.status_code,
                flow_run_id,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning(
                "[prefect] get_flow_run_logs error type=%s detail=%s", type(e).__name__, e
            )
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Workers / work pools
    # ------------------------------------------------------------------

    def get_work_pools(self, limit: int = 20) -> dict[str, Any]:
        """List available Prefect work pools.

        Args:
            limit: Maximum number of work pools to return.
        """
        body: dict[str, Any] = {"limit": min(limit, 200)}
        try:
            resp = self._get_client().post("/work_pools/filter", json=body)
            resp.raise_for_status()
            raw: list[dict[str, Any]] = resp.json()
            pools = [
                {
                    "id": p.get("id", ""),
                    "name": p.get("name", ""),
                    "type": p.get("type", ""),
                    "status": p.get("status", ""),
                    "is_paused": p.get("is_paused", False),
                    "concurrency_limit": p.get("concurrency_limit", None),
                }
                for p in raw
            ]
            return {"success": True, "work_pools": pools, "total": len(pools)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[prefect] get_work_pools HTTP failure status=%s",
                e.response.status_code,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning("[prefect] get_work_pools error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}

    def get_workers(self, work_pool_name: str, limit: int = 20) -> dict[str, Any]:
        """List workers registered in a work pool.

        Args:
            work_pool_name: The name of the work pool to query.
            limit: Maximum number of workers to return.
        """
        body: dict[str, Any] = {"limit": min(limit, 200)}
        try:
            resp = self._get_client().post(
                f"/work_pools/{quote(work_pool_name, safe='')}/workers/filter", json=body
            )
            resp.raise_for_status()
            raw: list[dict[str, Any]] = resp.json()
            workers = [
                {
                    "name": w.get("name", ""),
                    "status": w.get("status", ""),
                    "last_heartbeat_time": w.get("last_heartbeat_time", ""),
                    "work_pool_id": w.get("work_pool_id", ""),
                }
                for w in raw
            ]
            return {"success": True, "workers": workers, "total": len(workers)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[prefect] get_workers HTTP failure status=%s pool=%r",
                e.response.status_code,
                work_pool_name,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning("[prefect] get_workers error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Deployments
    # ------------------------------------------------------------------

    def get_deployments(self, limit: int = 20) -> dict[str, Any]:
        """List Prefect deployments.

        Args:
            limit: Maximum number of deployments to return.
        """
        body: dict[str, Any] = {"limit": min(limit, 200)}
        try:
            resp = self._get_client().post("/deployments/filter", json=body)
            resp.raise_for_status()
            raw: list[dict[str, Any]] = resp.json()
            deployments = [
                {
                    "id": d.get("id", ""),
                    "name": d.get("name", ""),
                    "flow_id": d.get("flow_id", ""),
                    "is_schedule_active": d.get("is_schedule_active", False),
                    "work_pool_name": d.get("work_pool_name", ""),
                    "last_polled": d.get("last_polled", ""),
                    "status": d.get("status", ""),
                    "tags": d.get("tags", []),
                }
                for d in raw
            ]
            return {"success": True, "deployments": deployments, "total": len(deployments)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[prefect] get_deployments HTTP failure status=%s",
                e.response.status_code,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning("[prefect] get_deployments error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}


def make_prefect_client(
    api_url: str | None,
    api_key: str | None = None,
    account_id: str | None = None,
    workspace_id: str | None = None,
) -> PrefectClient | None:
    """Build a configured PrefectClient, returning None if api_url is absent."""
    url = (api_url or "").strip()
    if not url:
        return None
    try:
        return PrefectClient(
            PrefectConfig(
                api_url=url,
                api_key=api_key or "",
                account_id=account_id or "",
                workspace_id=workspace_id or "",
            )
        )
    except Exception:
        return None
