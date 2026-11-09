"""Jenkins REST API client.

Wraps the Jenkins endpoints used to correlate builds/deployments with incidents:
recent builds, build console logs, the job list, and currently running builds.
Credentials come from the user's Jenkins integration stored locally or via env vars.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

import httpx
from pydantic import ValidationError

from infrastructure.observability.errors.service import capture_service_error
from integrations.jenkins import JenkinsConfig

logger = logging.getLogger(__name__)

_MAX_JOB_NAME_LEN = 256
_MAX_LOG_CHARS = 50_000
# Cap the number of jobs returned by discovery so a server with thousands of
# jobs cannot blow past the request timeout. list_jobs/list_running_builds
# report whether the cap truncated the result.
_MAX_JOBS = 200
# Jenkins folders nest jobs; descend this many levels when discovering jobs.
# Realistic folder hierarchies are 1-3 deep, so this comfortably covers them.
_MAX_FOLDER_DEPTH = 10

# Jenkins encodes a job's last-build status in a "color" field (a legacy ball-color scheme).
_COLOR_STATUS = {
    "blue": "SUCCESS",
    "green": "SUCCESS",
    "red": "FAILURE",
    "yellow": "UNSTABLE",
    "aborted": "ABORTED",
    "grey": "NOT_BUILT",
    "disabled": "DISABLED",
    "notbuilt": "NOT_BUILT",
}


def _safe_job_name(raw: str) -> str | None:
    """Validate a job name (top-level or folder path) before building a URL path.

    A '/' separates folder segments (e.g. ``team/payment-service``), which
    ``_job_api_path`` maps to Jenkins' nested ``/job/`` path. Rejects empty,
    oversized, traversal-prone, or malformed names (empty/whitespace segments,
    backslashes). Each segment is trimmed; the normalized display name is
    returned.
    """
    cleaned = (raw or "").strip().strip("/")
    if not cleaned or len(cleaned) > _MAX_JOB_NAME_LEN:
        return None
    if ".." in cleaned or "\\" in cleaned:
        return None
    segments = [segment.strip() for segment in cleaned.split("/")]
    if any(not segment for segment in segments):
        return None
    return "/".join(segments)


def _job_api_path(name: str) -> str:
    """Map a validated job name to its Jenkins API path.

    ``payment-service`` -> ``job/payment-service``;
    ``team/payment-service`` -> ``job/team/job/payment-service`` (folder jobs).
    Input is assumed already validated by ``_safe_job_name``.
    """
    return "/".join(f"job/{segment}" for segment in name.split("/"))


def _iso_from_ms(value: object) -> str:
    """Convert a Jenkins epoch-millisecond timestamp to an ISO-8601 UTC string."""
    if not isinstance(value, (int, float, str)):
        return ""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return ""
    if ms <= 0:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _status_from_color(color: object) -> tuple[str, bool]:
    """Map a Jenkins job "color" to a (status, is_building) pair.

    A trailing "_anime" suffix means a build is currently in progress.
    """
    raw = str(color or "").strip().lower()
    building = raw.endswith("_anime")
    base = raw[: -len("_anime")] if building else raw
    return _COLOR_STATUS.get(base, base.upper() or "UNKNOWN"), building


def _shape_build(job_name: str, build: dict[str, Any]) -> dict[str, Any]:
    """Normalize one raw Jenkins build object into our stable output shape."""
    result = build.get("result")
    building = bool(build.get("building")) or result is None
    return {
        "job": job_name,
        "number": build.get("number"),
        # result is null while a build is still running; surface RUNNING explicitly.
        "status": "RUNNING" if building else str(result or "UNKNOWN"),
        "building": building,
        "timestamp": _iso_from_ms(build.get("timestamp")),
        "duration_ms": build.get("duration", 0),
        "url": build.get("url", ""),
    }


def _shape_stage(stage: dict[str, Any]) -> dict[str, Any]:
    """Normalize one Pipeline Stage View stage into our stable output shape."""
    return {
        "name": stage.get("name", ""),
        "status": stage.get("status", ""),
        "duration_ms": stage.get("durationMillis", 0),
        "start_time": _iso_from_ms(stage.get("startTimeMillis")),
    }


def _as_dict(value: object) -> dict[str, Any]:
    """Coerce a decoded JSON value to a dict (defends against malformed responses)."""
    return value if isinstance(value, dict) else {}


def _as_dict_list(value: object) -> list[dict[str, Any]]:
    """Coerce a decoded JSON value to a list of dicts, dropping non-dict items."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _nested_jobs_tree(leaf_fields: str, depth: int) -> str:
    """Build a Jenkins ``tree`` query that descends folders ``depth`` levels.

    Each level requests ``leaf_fields`` plus a nested ``jobs[...]`` for the
    next level, so folder-organized jobs are returned alongside top-level
    ones. The deepest level also probes one level further with a bare
    ``jobs[name]`` (name only, no other fields) -- without it, a folder
    that exists exactly at the depth limit comes back with no ``jobs`` key
    at all (Jenkins only returns what the tree asked for), indistinguishable
    from a genuine leaf job. The probe lets ``_flatten_jobs`` tell the two
    apart and report depth truncation instead of silently treating an
    unexplored folder as a job with unknown status.
    """
    tree = f"{leaf_fields},jobs[name]"
    for _ in range(max(0, depth - 1)):
        tree = f"{leaf_fields},jobs[{tree}]"
    return f"jobs[{tree}]"


def _flatten_jobs(
    raw_jobs: list[dict[str, Any]], prefix: str = "", max_depth: int = 0
) -> tuple[list[tuple[str, dict]], bool]:
    """Flatten a (possibly nested) Jenkins jobs tree into ``(full_path, job)`` pairs.

    Returns ``(flat, depth_truncated)``. A node with a nested ``jobs`` list is
    a folder and is recursed into (its children's names are prefixed with
    ``folder/``); leaf jobs (no ``jobs`` key) are appended with their full
    path. At ``max_depth`` the tree query only probed one level further with
    bare names (see ``_nested_jobs_tree``), so a folder still reporting a
    non-empty ``jobs`` list here has children beyond the requested depth that
    were never fully fetched -- it's dropped (it isn't a job) and
    ``depth_truncated`` is set, rather than misreading it as a job.
    """
    flat: list[tuple[str, dict]] = []
    depth_truncated = False
    for job in raw_jobs:
        name = str(job.get("name", "")).strip()
        if not name:
            continue
        full_path = f"{prefix}{name}"
        nested = job.get("jobs")
        if isinstance(nested, list):
            if max_depth <= 0:
                if nested:
                    depth_truncated = True
                continue
            child_flat, child_truncated = _flatten_jobs(
                _as_dict_list(nested), prefix=f"{full_path}/", max_depth=max_depth - 1
            )
            flat.extend(child_flat)
            depth_truncated = depth_truncated or child_truncated
        else:
            flat.append((full_path, job))
    return flat, depth_truncated


def _coerce_build_number(value: object) -> int | None:
    """Coerce a build number to a positive int, or None if invalid.

    Jenkins build numbers start at 1; reject zero/negative and non-numeric input.
    """
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None


class JenkinsClient:
    """Synchronous client for the Jenkins REST API."""

    def __init__(self, config: JenkinsConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.api_base_url,
                auth=self.config.auth,
                timeout=self.config.timeout_seconds,
            )
        return self._client

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> JenkinsClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def list_builds(
        self,
        job_name: str,
        limit: int = 10,
        status: str = "",
    ) -> dict[str, Any]:
        """List recent builds for a job, newest first.

        Args:
            job_name: The Jenkins job (project) name.
            limit: Maximum number of builds to return (capped at 50).
            status: Optional status filter, e.g. "FAILURE", "SUCCESS", "RUNNING".
        """
        safe_name = _safe_job_name(job_name)
        if not safe_name:
            return {"success": False, "error": "invalid job name"}
        # Cap the server-side fetch so we never transfer a job's full history.
        # With a status filter we pull a wider window so the filter has enough
        # rows to work with; otherwise fetch just what the caller asked for.
        fetch_count = 50 if status else max(1, min(limit, 50))
        tree = f"builds[number,result,timestamp,duration,url,building]{{0,{fetch_count}}}"
        try:
            resp = self._get_client().get(
                f"/{_job_api_path(safe_name)}/api/json", params={"tree": tree}
            )
            resp.raise_for_status()
            data = _as_dict(resp.json())
            builds = [_shape_build(safe_name, b) for b in _as_dict_list(data.get("builds"))]
            if status:
                wanted = status.strip().upper()
                builds = [b for b in builds if b["status"] == wanted]
            builds = builds[: max(1, min(limit, 50))]
            failed = [b for b in builds if b["status"] == "FAILURE"]
            return {
                "success": True,
                "job": safe_name,
                "builds": builds,
                "failed_builds": failed,
                "total": len(builds),
            }
        except Exception as exc:
            return self._error("list_builds", exc, {"job": job_name, "status": status})

    def get_build_log(
        self,
        job_name: str,
        build_number: int,
        max_chars: int = _MAX_LOG_CHARS,
    ) -> dict[str, Any]:
        """Fetch the console log for a specific build, tail-truncated to ``max_chars``."""
        safe_name = _safe_job_name(job_name)
        if not safe_name:
            return {"success": False, "error": "invalid job name"}
        number = _coerce_build_number(build_number)
        if number is None:
            return {"success": False, "error": "invalid build number"}

        try:
            resp = self._get_client().get(f"/{_job_api_path(safe_name)}/{number}/consoleText")
            resp.raise_for_status()
            text = resp.text
            truncated = len(text) > max_chars
            # Keep the tail — failures and stack traces live at the end of a build log.
            log = text[-max_chars:] if truncated else text
            return {
                "success": True,
                "job": safe_name,
                "build_number": number,
                "log": log,
                "truncated": truncated,
            }
        except Exception as exc:
            return self._error("get_build_log", exc, {"job": job_name, "build": build_number})

    def get_pipeline_stages(self, job_name: str, build_number: int) -> dict[str, Any]:
        """Fetch pipeline stages for a build via the Stage View API (wfapi).

        Freestyle jobs (and servers without the Pipeline Stage View plugin) have
        no stages: those return ``is_pipeline=False`` with an empty stage list
        rather than an error.
        """
        safe_name = _safe_job_name(job_name)
        if not safe_name:
            return {"success": False, "error": "invalid job name"}
        number = _coerce_build_number(build_number)
        if number is None:
            return {"success": False, "error": "invalid build number"}

        try:
            resp = self._get_client().get(f"/{_job_api_path(safe_name)}/{number}/wfapi/describe")
            if resp.status_code == HTTPStatus.NOT_FOUND:
                # Not a Pipeline job, or the Stage View plugin is absent.
                return {
                    "success": True,
                    "job": safe_name,
                    "build_number": number,
                    "is_pipeline": False,
                    "stages": [],
                }
            resp.raise_for_status()
            data = _as_dict(resp.json())
            stages = [_shape_stage(s) for s in _as_dict_list(data.get("stages"))]
            return {
                "success": True,
                "job": safe_name,
                "build_number": number,
                "is_pipeline": True,
                "status": data.get("status", ""),
                "stages": stages,
            }
        except Exception as exc:
            return self._error("get_pipeline_stages", exc, {"job": job_name, "build": build_number})

    def list_jobs(self) -> dict[str, Any]:
        """List jobs with their last-build status (decoded from the color field).

        Recurses Jenkins folders up to ``_MAX_FOLDER_DEPTH`` (folder jobs are
        reported by their full ``folder/job`` path), capped at ``_MAX_JOBS``;
        ``truncated`` is True when the cap or depth limit dropped jobs.
        """
        leaf = "name,url,color,lastBuild[number,result,timestamp,url]"
        tree = _nested_jobs_tree(leaf, _MAX_FOLDER_DEPTH)
        try:
            resp = self._get_client().get("/api/json", params={"tree": tree})
            resp.raise_for_status()
            data = _as_dict(resp.json())
            flat, depth_truncated = _flatten_jobs(
                _as_dict_list(data.get("jobs")), max_depth=_MAX_FOLDER_DEPTH - 1
            )
            jobs = []
            for full_path, job in flat[:_MAX_JOBS]:
                status, building = _status_from_color(job.get("color"))
                last = _as_dict(job.get("lastBuild"))
                jobs.append(
                    {
                        "name": full_path,
                        "url": job.get("url", ""),
                        "status": status,
                        "building": building,
                        "last_build_number": last.get("number"),
                        "last_build_at": _iso_from_ms(last.get("timestamp")),
                    }
                )
            return {
                "success": True,
                "jobs": jobs,
                "total": len(jobs),
                "truncated": len(flat) > _MAX_JOBS or depth_truncated,
            }
        except Exception as exc:
            return self._error("list_jobs", exc, {})

    def list_running_builds(self) -> dict[str, Any]:
        """List builds currently in progress across all jobs.

        Recurses Jenkins folders up to ``_MAX_FOLDER_DEPTH`` (5 most-recent builds
        per job), scanning at most ``_MAX_JOBS`` jobs; ``truncated`` is True when
        the cap or depth limit dropped jobs. Running builds are reported by their
        full ``folder/job`` path.
        """
        leaf = "name,builds[number,building,result,timestamp,url]{0,5}"
        tree = _nested_jobs_tree(leaf, _MAX_FOLDER_DEPTH)
        try:
            resp = self._get_client().get("/api/json", params={"tree": tree})
            resp.raise_for_status()
            data = _as_dict(resp.json())
            flat, depth_truncated = _flatten_jobs(
                _as_dict_list(data.get("jobs")), max_depth=_MAX_FOLDER_DEPTH - 1
            )
            running = []
            for full_path, job in flat[:_MAX_JOBS]:
                for build in _as_dict_list(job.get("builds")):
                    if build.get("building"):
                        running.append(_shape_build(full_path, build))
            return {
                "success": True,
                "running_builds": running,
                "total": len(running),
                "truncated": len(flat) > _MAX_JOBS or depth_truncated,
            }
        except Exception as exc:
            return self._error("list_running_builds", exc, {})

    def _error(
        self,
        method: str,
        exc: Exception,
        extras: dict[str, Any],
    ) -> dict[str, Any]:
        capture_service_error(
            exc, logger=logger, integration="jenkins", method=method, extras=extras
        )
        if isinstance(exc, httpx.HTTPStatusError):
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        return {"success": False, "error": str(exc)}


def make_jenkins_client(
    base_url: str | None,
    username: str | None = None,
    api_token: str | None = None,
) -> JenkinsClient | None:
    """Build a configured JenkinsClient.

    Returns None unless URL, username, and token are all present. Jenkins Basic
    auth sends ``username:api_token``; an empty username yields a ``:token`` pair
    that Jenkins rejects with HTTPStatus.UNAUTHORIZED, so the factory refuses
    to build such a client—every caller gets a clean "not configured" path
    instead of an HTTPStatus.UNAUTHORIZED response.
    """
    url = (base_url or "").strip()
    user = (username or "").strip()
    token = (api_token or "").strip()
    if not (url and user and token):
        return None
    try:
        config = JenkinsConfig(base_url=url, username=user, api_token=token)
    except ValidationError:
        return None
    return JenkinsClient(config)
