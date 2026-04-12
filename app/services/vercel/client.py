"""Vercel REST API client.

Wraps the Vercel API endpoints used for deployment status and log retrieval.
Credentials come from the user's Vercel integration stored locally or via env vars.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import field_validator

from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.vercel.com"
_MAX_VERCEL_PATH_SEGMENT_LEN = 256
# Vercel project and deployment IDs are opaque tokens (no slashes or traversal).
_VERCEL_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DEFAULT_TIMEOUT = 30
# Runtime logs can take a long time (large limit, slow server-side aggregation, stream+json).
_RUNTIME_LOGS_READ_ATTEMPTS = 3
_RUNTIME_LOGS_READ_TIMEOUT_DEFAULT = 600.0


def _scrub_log_fragment(value: object) -> str:
    """Make user-controlled strings safe for single-line log records (avoid log injection)."""
    text = str(value)
    return text.replace("\r", "\\r").replace("\n", "\\n")


def _safe_vercel_path_segment(raw: str) -> str | None:
    cleaned = (raw or "").strip()
    if not cleaned or len(cleaned) > _MAX_VERCEL_PATH_SEGMENT_LEN:
        return None
    if ".." in cleaned or "//" in cleaned:
        return None
    if not _VERCEL_PATH_SEGMENT_RE.fullmatch(cleaned):
        return None
    return cleaned


def _runtime_logs_read_timeout_seconds() -> float:
    """Seconds to wait per read for runtime logs (env VERCEL_RUNTIME_LOGS_READ_TIMEOUT)."""
    read_s = _RUNTIME_LOGS_READ_TIMEOUT_DEFAULT
    raw = os.getenv("VERCEL_RUNTIME_LOGS_READ_TIMEOUT", "").strip()
    if raw:
        try:
            read_s = max(30.0, float(raw))
        except ValueError:
            logger.warning(
                "Invalid VERCEL_RUNTIME_LOGS_READ_TIMEOUT=%r; using default %s",
                raw,
                read_s,
            )
    return read_s


def _runtime_logs_http_timeout() -> httpx.Timeout:
    read_s = _runtime_logs_read_timeout_seconds()
    connect_s = min(30.0, read_s)
    return httpx.Timeout(connect=connect_s, read=read_s, write=read_s, pool=connect_s)


def _normalize_git_meta(meta: object) -> dict[str, str]:
    meta_dict = meta if isinstance(meta, dict) else {}
    return {
        "github_commit_sha": str(meta_dict.get("githubCommitSha", "")).strip(),
        "github_commit_message": str(meta_dict.get("githubCommitMessage", "")).strip(),
        "github_commit_ref": str(meta_dict.get("githubCommitRef", "")).strip(),
        "github_repo": str(meta_dict.get("githubRepo", "")).strip(),
    }


def _extract_event_text(event: dict[str, Any]) -> str:
    text = event.get("text")
    if text is not None:
        return str(text)
    payload = event.get("payload")
    if isinstance(payload, dict):
        payload_text = payload.get("text")
        if payload_text is not None:
            return str(payload_text)
    return ""


def _extract_runtime_log_message(log: dict[str, Any]) -> str:
    message = log.get("message")
    if message is not None:
        return str(message)
    payload = log.get("payload")
    if isinstance(payload, dict):
        for key in ("text", "message", "body"):
            value = payload.get(key)
            if value is not None:
                return str(value)
    if payload is not None and not isinstance(payload, dict):
        return str(payload)
    return ""


def _parse_runtime_logs_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        logs = payload.get("logs", [])
        return [item for item in logs if isinstance(item, dict)] if isinstance(logs, list) else []
    return []


def _append_parsed_runtime_stream_value(
    parsed: Any,
    bucket: list[dict[str, Any]],
    *,
    limit: int,
) -> None:
    """Expand one decoded JSON value from a runtime-log stream into ``bucket`` (cap at ``limit``)."""
    if isinstance(parsed, dict):
        nested = parsed.get("logs")
        if isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    bucket.append(item)
                    if len(bucket) >= limit:
                        return
            return
        bucket.append(parsed)
        return
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                bucket.append(item)
                if len(bucket) >= limit:
                    return


def _ingest_runtime_log_stream_line(line: str, bucket: list[dict[str, Any]], limit: int) -> bool:
    """Parse a single text line from the stream; return True if ``bucket`` has reached ``limit``."""
    stripped = line.strip()
    if not stripped:
        return len(bucket) >= limit
    if stripped.startswith("data:"):
        stripped = stripped[5:].lstrip()
    try:
        parsed: Any = json.loads(stripped)
    except json.JSONDecodeError:
        return len(bucket) >= limit
    _append_parsed_runtime_stream_value(parsed, bucket, limit=limit)
    return len(bucket) >= limit


def _collect_runtime_logs_from_stream(response: httpx.Response, limit: int) -> list[dict[str, Any]]:
    """Read Vercel's streamed runtime logs (NDJSON / stream+json); stop after ``limit`` dict rows."""
    bucket: list[dict[str, Any]] = []
    for line in response.iter_lines():
        if _ingest_runtime_log_stream_line(line, bucket, limit):
            break
    return bucket[:limit]


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
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning("[vercel] list_projects error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}

    def get_project(self, project_id_or_name: str) -> dict[str, Any]:
        """Fetch project details including the current production deployment (no deployment list)."""
        cleaned = (project_id_or_name or "").strip()
        if not cleaned:
            return {"success": False, "error": "project id or name is required"}
        params: dict[str, Any] = {}
        params.update(self.config.team_params)
        try:
            safe = quote(cleaned, safe="")
            resp = self._get_client().get(f"/v9/projects/{safe}", params=params)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                return {"success": False, "error": "unexpected project response"}
            targets = data.get("targets")
            prod: dict[str, Any] = {}
            if isinstance(targets, dict):
                raw_prod = targets.get("production")
                if isinstance(raw_prod, dict):
                    prod = raw_prod
            prod_id = str(prod.get("id", "")).strip()
            return {
                "success": True,
                "project": data,
                "production_deployment_id": prod_id,
                "production_target": prod,
            }
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[vercel] get_project HTTP failure status=%s project=%r",
                e.response.status_code,
                cleaned,
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning("[vercel] get_project error type=%s detail=%s", type(e).__name__, e)
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
                    "meta": _normalize_git_meta(d.get("meta", {})),
                    "raw_meta": d.get("meta", {}) if isinstance(d.get("meta", {}), dict) else {},
                }
                for d in data.get("deployments", [])
            ]
            return {"success": True, "deployments": deployments, "total": len(deployments)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[vercel] list_deployments HTTP failure status=%s project=%s state=%s",
                e.response.status_code,
                _scrub_log_fragment(project_id),
                _scrub_log_fragment(state),
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning("[vercel] list_deployments error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}

    def get_deployment(self, deployment_id: str) -> dict[str, Any]:
        """Fetch full details for a single deployment including build errors and git metadata."""
        safe_id = _safe_vercel_path_segment(deployment_id)
        if not safe_id:
            return {"success": False, "error": "invalid deployment id"}
        params: dict[str, Any] = {}
        params.update(self.config.team_params)
        try:
            resp = self._get_client().get(f"/v13/deployments/{safe_id}", params=params)
            resp.raise_for_status()
            data = resp.json()
            raw_meta = data.get("meta", {}) if isinstance(data.get("meta", {}), dict) else {}
            return {
                "success": True,
                "deployment": {
                    "id": data.get("id", ""),
                    "url": data.get("url", ""),
                    "name": data.get("name", ""),
                    "state": data.get("readyState", ""),
                    "error": data.get("errorMessage", "") or data.get("errorCode", ""),
                    "created_at": data.get("createdAt", ""),
                    "meta": _normalize_git_meta(raw_meta),
                    "raw_meta": raw_meta,
                    "build": data.get("build", {}),
                },
            }
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[vercel] get_deployment HTTP failure status=%s id=%s",
                e.response.status_code,
                _scrub_log_fragment(deployment_id),
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning("[vercel] get_deployment error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}

    def get_deployment_events(self, deployment_id: str, limit: int = 100) -> dict[str, Any]:
        """Fetch the build and runtime event stream for a deployment."""
        safe_id = _safe_vercel_path_segment(deployment_id)
        if not safe_id:
            return {"success": False, "error": "invalid deployment id"}
        params: dict[str, Any] = {"limit": min(limit, 2000)}
        params.update(self.config.team_params)
        try:
            resp = self._get_client().get(f"/v3/deployments/{safe_id}/events", params=params)
            resp.raise_for_status()
            data = resp.json()
            raw_events = data if isinstance(data, list) else data.get("events", [])
            events = []
            for ev in raw_events:
                if not isinstance(ev, dict):
                    continue
                events.append(
                    {
                        "id": str(ev.get("id", "")),
                        "type": ev.get("type", ""),
                        "created": ev.get("created", ""),
                        "text": _extract_event_text(ev),
                    }
                )
            return {"success": True, "events": events, "total": len(events)}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[vercel] get_deployment_events HTTP failure status=%s id=%s",
                e.response.status_code,
                _scrub_log_fragment(deployment_id),
            )
            return {
                "success": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.warning(
                "[vercel] get_deployment_events error type=%s detail=%s", type(e).__name__, e
            )
            return {"success": False, "error": str(e)}

    def get_runtime_logs(
        self,
        deployment_id: str,
        limit: int = 100,
        *,
        project_id: str = "",
    ) -> dict[str, Any]:
        """Fetch serverless function runtime logs for a deployment.

        Per Vercel (`GET /v1/projects/{projectId}/deployments/{deploymentId}/runtime-logs
        <https://vercel.com/docs/rest-api/logs/get-logs-for-a-deployment>`_), the response is
        ``Content-Type: application/stream+json``: a **stream** of JSON objects, not one array.
        This client uses :meth:`httpx.Client.stream` and :meth:`httpx.Response.iter_lines` to read
        incrementally (line-delimited JSON) until ``limit`` rows are collected or the stream ends.
        """
        params: dict[str, Any] = {"limit": min(limit, 2000)}
        params.update(self.config.team_params)
        safe_deployment = _safe_vercel_path_segment(deployment_id)
        if not safe_deployment:
            return {"success": False, "error": "invalid deployment id"}
        cleaned_project = (project_id or "").strip()
        if cleaned_project:
            safe_project = _safe_vercel_path_segment(cleaned_project)
            if not safe_project:
                return {"success": False, "error": "invalid project id"}
            path = f"/v1/projects/{safe_project}/deployments/{safe_deployment}/runtime-logs"
        else:
            path = f"/v1/deployments/{safe_deployment}/logs"

        cap = min(limit, 2000)
        stream_headers = {
            **self.config.headers,
            "Accept": "application/stream+json, application/x-ndjson, application/json",
        }

        last_retryable_detail = ""
        last_retryable_kind = ""
        for attempt in range(1, _RUNTIME_LOGS_READ_ATTEMPTS + 1):
            try:
                http = self._get_client()
                with http.stream(
                    "GET",
                    path,
                    params=params,
                    headers=stream_headers,
                    timeout=_runtime_logs_http_timeout(),
                ) as resp:
                    resp.raise_for_status()
                    raw_logs = _collect_runtime_logs_from_stream(resp, cap)
                logs = [
                    {
                        "id": log.get("id", "") or log.get("rowId", ""),
                        "created_at": log.get("createdAt", "") or log.get("timestampInMs", ""),
                        "payload": log.get("payload", {}),
                        "message": _extract_runtime_log_message(log),
                        "type": log.get("type", "") or log.get("level", ""),
                        "source": log.get("source", ""),
                        "level": log.get("level", ""),
                        "request_path": log.get("requestPath", ""),
                        "request_method": str(
                            log.get("requestMethod", "") or log.get("method", "") or ""
                        ).strip(),
                        "status_code": log.get("responseStatusCode", ""),
                        "domain": log.get("domain", ""),
                    }
                    for log in raw_logs
                    if isinstance(log, dict)
                ]
                return {"success": True, "logs": logs, "total": len(logs)}
            except (httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError) as exc:
                last_retryable_detail = str(exc)
                last_retryable_kind = type(exc).__name__
                logger.warning(
                    "[vercel] get_runtime_logs %s attempt %s/%s deployment=%s",
                    last_retryable_kind,
                    attempt,
                    _RUNTIME_LOGS_READ_ATTEMPTS,
                    _scrub_log_fragment(deployment_id),
                )
                if attempt >= _RUNTIME_LOGS_READ_ATTEMPTS:
                    break
                time.sleep(min(8.0, 2.0**attempt))
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return {"success": True, "logs": [], "total": 0}
                logger.warning(
                    "[vercel] get_runtime_logs HTTP failure status=%s id=%s",
                    e.response.status_code,
                    _scrub_log_fragment(deployment_id),
                )
                return {
                    "success": False,
                    "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                }
            except Exception as e:
                logger.warning(
                    "[vercel] get_runtime_logs error type=%s detail=%s", type(e).__name__, e
                )
                return {"success": False, "error": str(e)}

        detail = last_retryable_detail or "Transient runtime log transport error"
        if last_retryable_kind == "ReadTimeout":
            read_s = _runtime_logs_read_timeout_seconds()
            return {
                "success": False,
                "error": (
                    f"{detail} (after {_RUNTIME_LOGS_READ_ATTEMPTS} attempts, "
                    f"{read_s:g}s read timeout each; set VERCEL_RUNTIME_LOGS_READ_TIMEOUT to increase)"
                ),
            }

        kind = last_retryable_kind or "transport error"
        return {
            "success": False,
            "error": (
                f"{detail} (after {_RUNTIME_LOGS_READ_ATTEMPTS} attempts while reading "
                f"runtime logs; last error type: {kind})"
            ),
        }


def make_vercel_client(api_token: str | None, team_id: str | None = None) -> VercelClient | None:
    """Build a configured VercelClient, returning None if the token is absent."""
    token = (api_token or "").strip()
    if not token:
        return None
    try:
        return VercelClient(VercelConfig(api_token=token, team_id=team_id or ""))
    except Exception:
        return None
