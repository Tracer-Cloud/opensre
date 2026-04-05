"""Vercel REST API client.

Wraps the Vercel API endpoints used for deployment status and log retrieval.
Credentials come from the user's Vercel integration stored locally or via env vars.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import field_validator

from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.vercel.com"
_DEFAULT_TIMEOUT = 30


class VercelConfig(StrictConfigModel):
    api_token: str
    team_id: str = ""
    integration_id: str = ""

    @field_validator("api_token", mode="before")
    @classmethod
    def _normalize_token(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("team_id", mode="before")
    @classmethod
    def _normalize_team_id(cls, value: object) -> str:
        return str(value or "").strip()

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    @property
    def team_params(self) -> dict[str, str]:
        return {"teamId": self.team_id} if self.team_id else {}


class VercelClient:
    """Synchronous client for the Vercel REST API."""

    def __init__(self, config: VercelConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=_BASE_URL,
                headers=self.config.headers,
                timeout=_DEFAULT_TIMEOUT,
            )
        return self._client

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> VercelClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def is_configured(self) -> bool:
        return bool(self.config.api_token)

    def list_projects(self, limit: int = 20) -> dict[str, Any]:
        """List projects accessible to the API token."""
        params: dict[str, Any] = {"limit": min(limit, 100)}
        params.update(self.config.team_params)
        try:
            resp = self._get_client().get("/v9/projects", params=params)
            resp.raise_for_status()
            data = resp.json()
            projects = [
                {
                    "id": p.get("id", ""),
                    "name": p.get("name", ""),
                    "framework": p.get("framework", ""),
                    "updated_at": p.get("updatedAt", ""),
                }
                for p in data.get("projects", [])
            ]
            return {"success": True, "projects": projects, "total": len(projects)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[vercel] list_projects HTTP failure status=%s",
                e.response.status_code,
            )
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[vercel] list_projects error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}

    def list_deployments(
        self,
        project_id: str = "",
        limit: int = 10,
        state: str = "",
    ) -> dict[str, Any]:
        """List recent deployments, optionally filtered by project and state.

        Args:
            project_id: Vercel project ID to scope the query.
            limit: Maximum number of deployments to return (capped at 100).
            state: Deployment state filter — READY, ERROR, BUILDING, or CANCELED.
        """
        params: dict[str, Any] = {"limit": min(limit, 100)}
        params.update(self.config.team_params)
        if project_id:
            params["projectId"] = project_id
        if state:
            params["state"] = state.upper()
        try:
            resp = self._get_client().get("/v6/deployments", params=params)
            resp.raise_for_status()
            data = resp.json()
            deployments = [
                {
                    "id": d.get("uid", ""),
                    "name": d.get("name", ""),
                    "url": d.get("url", ""),
                    "state": d.get("state", ""),
                    "created_at": d.get("createdAt", ""),
                    "ready_at": d.get("ready", ""),
                    "error": d.get("errorMessage", "") or d.get("errorCode", ""),
                    "meta": {
                        "github_commit_sha": d.get("meta", {}).get("githubCommitSha", ""),
                        "github_commit_message": d.get("meta", {}).get("githubCommitMessage", ""),
                        "github_commit_ref": d.get("meta", {}).get("githubCommitRef", ""),
                        "github_repo": d.get("meta", {}).get("githubRepo", ""),
                    },
                }
                for d in data.get("deployments", [])
            ]
            return {"success": True, "deployments": deployments, "total": len(deployments)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[vercel] list_deployments HTTP failure status=%s project=%r state=%r",
                e.response.status_code,
                project_id,
                state,
            )
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[vercel] list_deployments error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}

    def get_deployment(self, deployment_id: str) -> dict[str, Any]:
        """Fetch full details for a single deployment including build errors and git metadata."""
        params: dict[str, Any] = {}
        params.update(self.config.team_params)
        try:
            resp = self._get_client().get(f"/v13/deployments/{deployment_id}", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "success": True,
                "deployment": {
                    "id": data.get("id", ""),
                    "url": data.get("url", ""),
                    "name": data.get("name", ""),
                    "state": data.get("readyState", ""),
                    "error": data.get("errorMessage", "") or data.get("errorCode", ""),
                    "created_at": data.get("createdAt", ""),
                    "meta": data.get("meta", {}),
                    "build": data.get("build", {}),
                },
            }
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[vercel] get_deployment HTTP failure status=%s id=%r",
                e.response.status_code,
                deployment_id,
            )
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[vercel] get_deployment error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}

    def get_deployment_events(self, deployment_id: str, limit: int = 100) -> dict[str, Any]:
        """Fetch the build and runtime event stream for a deployment."""
        params: dict[str, Any] = {"limit": min(limit, 2000)}
        params.update(self.config.team_params)
        try:
            resp = self._get_client().get(f"/v3/deployments/{deployment_id}/events", params=params)
            resp.raise_for_status()
            data = resp.json()
            raw_events = data if isinstance(data, list) else data.get("events", [])
            events = []
            for ev in raw_events:
                text = ev.get("text") or (ev.get("payload") or {}).get("text") or ""
                events.append({
                    "type": ev.get("type", ""),
                    "created": ev.get("created", ""),
                    "text": str(text),
                })
            return {"success": True, "events": events, "total": len(events)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[vercel] get_deployment_events HTTP failure status=%s id=%r",
                e.response.status_code,
                deployment_id,
            )
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[vercel] get_deployment_events error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}

    def get_runtime_logs(self, deployment_id: str, limit: int = 100) -> dict[str, Any]:
        """Fetch serverless function runtime logs (stdout/stderr) for a deployment."""
        params: dict[str, Any] = {"limit": min(limit, 2000)}
        params.update(self.config.team_params)
        try:
            resp = self._get_client().get(f"/v1/deployments/{deployment_id}/logs", params=params)
            resp.raise_for_status()
            data = resp.json()
            raw_logs = data if isinstance(data, list) else data.get("logs", [])
            logs = [
                {
                    "id": log.get("id", ""),
                    "created_at": log.get("createdAt", ""),
                    "payload": log.get("payload", {}),
                    "type": log.get("type", ""),
                    "source": log.get("source", ""),
                }
                for log in raw_logs
            ]
            return {"success": True, "logs": logs, "total": len(logs)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[vercel] get_runtime_logs HTTP failure status=%s id=%r",
                e.response.status_code,
                deployment_id,
            )
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[vercel] get_runtime_logs error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}


def make_vercel_client(api_token: str | None, team_id: str | None = None) -> VercelClient | None:
    """Build a configured VercelClient, returning None if the token is absent."""
    token = (api_token or "").strip()
    if not token:
        return None
    try:
        return VercelClient(VercelConfig(api_token=token, team_id=team_id or ""))
    except Exception:
        return None
