# ======== from tools/jenkins_tool/ ========

"""Jenkins CI/CD investigation tools.

Surfaces recent builds, build logs, the job list, and running builds so the
investigation pipeline can answer: "did a recent build or deployment coincide
with this alert?"
"""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.jenkins import jenkins_config_from_env
from integrations.jenkins.client import JenkinsClient, make_jenkins_client
from integrations.jenkins.tools._evidence import (
    map_get_jenkins_build_log,
    map_get_jenkins_pipeline_stages,
    map_list_jenkins_builds,
    map_list_jenkins_jobs,
    map_list_jenkins_running_builds,
)


def _jenkins_available(sources: dict) -> bool:
    return bool(sources.get("jenkins", {}).get("connection_verified"))


def _jenkins_creds(jk: dict) -> dict[str, Any]:
    # The resolved source dict stores connection fields as base_url/username/api_token
    # (from JenkinsConfig.model_dump); map them to the tool's param names.
    return {
        "jenkins_url": jk.get("base_url"),
        "jenkins_user": jk.get("username"),
        "jenkins_token": jk.get("api_token"),
    }


def _resolve_client(
    jenkins_url: str | None,
    jenkins_user: str | None,
    jenkins_token: str | None,
) -> JenkinsClient | None:
    """Build a client from explicit args, falling back to env-var config.

    Requires BOTH url and token to be explicitly present to take the explicit
    path; a half-supplied pair falls through to env resolution rather than
    silently mixing an explicit URL with an env token (or vice versa).
    """
    if all([jenkins_url, jenkins_token]):
        env = jenkins_config_from_env()
        effective_user = jenkins_user or (env.username if env else "")
        # make_jenkins_client returns None when the username is empty, so an
        # explicit URL+token without a resolvable username surfaces a clean
        # "not configured" error rather than a 401.
        return make_jenkins_client(jenkins_url, effective_user, jenkins_token)
    env = jenkins_config_from_env()
    # jenkins_config_from_env only requires url+token, so guard on is_configured
    # (which also requires username) to avoid building an empty-username client
    # that would 401 — same completeness check as load_env_integrations.
    if env is None or not env.is_configured:
        return None
    return make_jenkins_client(env.base_url, env.username, env.api_token)


def _not_configured(payload_key: str) -> dict[str, Any]:
    return tool_unavailable(
        "jenkins", "jenkins integration is not configured.", **{payload_key: []}
    )


# ---------------------------------------------------------------------------
# list_jenkins_builds
# ---------------------------------------------------------------------------


def _list_jenkins_builds_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    # job_name is supplied by the LLM (a required tool arg); provide it as a
    # default only if the resolved source already carries one.
    jk = sources.get("jenkins", {})
    return {
        "job_name": jk.get("job_name", ""),
        "limit": 10,
        "status": jk.get("status", ""),
        **_jenkins_creds(jk),
    }


@tool(
    name="list_jenkins_builds",
    source="jenkins",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description="List recent Jenkins builds for a job with status and timestamp.",
    use_cases=[
        "Checking whether a recent build or deployment coincided with the alert",
        "Identifying which build failed and when",
        "Correlating a deployment window with downstream errors in logs or metrics",
    ],
    requires=["job_name"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "job_name": {"type": "string"},
            "limit": {"type": "integer", "default": 10},
            "status": {
                "type": "string",
                "default": "",
                "description": "Optional filter: SUCCESS, FAILURE, RUNNING, ABORTED",
            },
            "jenkins_url": {"type": "string"},
            "jenkins_user": {"type": "string"},
            "jenkins_token": {"type": "string"},
        },
        "required": ["job_name"],
    },
    outputs={
        "builds": "Recent builds with status, timestamp, duration, and url",
        "failed_builds": "Subset of builds in FAILURE state",
    },
    is_available=_jenkins_available,
    extract_params=_list_jenkins_builds_extract_params,
    evidence_mapper=map_list_jenkins_builds,
)
def list_jenkins_builds(
    job_name: str,
    limit: int = 10,
    status: str = "",
    jenkins_url: str | None = None,
    jenkins_user: str | None = None,
    jenkins_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List recent builds for a Jenkins job."""
    client = _resolve_client(jenkins_url, jenkins_user, jenkins_token)
    if client is None:
        return _not_configured("builds")
    with client:
        result = client.list_builds(job_name, limit=limit, status=status)
    if not result.get("success"):
        return tool_unavailable("jenkins", result.get("error", "unknown error"), builds=[])
    return {
        "source": "jenkins",
        "available": True,
        "job": result.get("job", job_name),
        "builds": result.get("builds", []),
        "failed_builds": result.get("failed_builds", []),
        "total": result.get("total", 0),
    }


# ---------------------------------------------------------------------------
# get_jenkins_build_log
# ---------------------------------------------------------------------------


def _get_jenkins_build_log_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    # job_name and build_number are supplied by the LLM (required tool args).
    jk = sources.get("jenkins", {})
    return {
        "job_name": jk.get("job_name", ""),
        "build_number": jk.get("build_number", 0),
        **_jenkins_creds(jk),
    }


@tool(
    name="get_jenkins_build_log",
    source="jenkins",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description="Fetch the console log for a specific Jenkins build.",
    use_cases=[
        "Reading the error output of a failed build",
        "Finding the stack trace or failing step that broke a deployment",
    ],
    requires=["job_name", "build_number"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "job_name": {"type": "string"},
            "build_number": {"type": "integer"},
            "jenkins_url": {"type": "string"},
            "jenkins_user": {"type": "string"},
            "jenkins_token": {"type": "string"},
        },
        "required": ["job_name", "build_number"],
    },
    outputs={
        "log": "Console log text (tail-truncated for large logs)",
        "truncated": "Whether the log was truncated",
    },
    is_available=_jenkins_available,
    extract_params=_get_jenkins_build_log_extract_params,
    evidence_mapper=map_get_jenkins_build_log,
)
def get_jenkins_build_log(
    job_name: str,
    build_number: int,
    jenkins_url: str | None = None,
    jenkins_user: str | None = None,
    jenkins_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch the console log for a specific Jenkins build."""
    client = _resolve_client(jenkins_url, jenkins_user, jenkins_token)
    if client is None:
        return tool_unavailable("jenkins", "jenkins integration is not configured.", log="")
    with client:
        result = client.get_build_log(job_name, build_number)
    if not result.get("success"):
        return tool_unavailable("jenkins", result.get("error", "unknown error"), log="")
    return {
        "source": "jenkins",
        "available": True,
        "job": result.get("job", job_name),
        "build_number": result.get("build_number", build_number),
        "log": result.get("log", ""),
        "truncated": result.get("truncated", False),
    }


# ---------------------------------------------------------------------------
# get_jenkins_pipeline_stages
# ---------------------------------------------------------------------------


def _get_jenkins_pipeline_stages_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    jk = sources.get("jenkins", {})
    return {
        "job_name": jk.get("job_name", ""),
        "build_number": jk.get("build_number", 0),
        **_jenkins_creds(jk),
    }


@tool(
    name="get_jenkins_pipeline_stages",
    source="jenkins",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description="List the pipeline stages of a Jenkins build with per-stage status and duration.",
    use_cases=[
        "Identifying which pipeline stage failed in a deployment",
        "Seeing how long each stage took to spot a slow or stuck stage",
    ],
    requires=["job_name", "build_number"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "job_name": {"type": "string"},
            "build_number": {"type": "integer"},
            "jenkins_url": {"type": "string"},
            "jenkins_user": {"type": "string"},
            "jenkins_token": {"type": "string"},
        },
        "required": ["job_name", "build_number"],
    },
    outputs={
        "stages": "Pipeline stages with name, status, and duration",
        "is_pipeline": "False for freestyle jobs (no stages)",
    },
    is_available=_jenkins_available,
    extract_params=_get_jenkins_pipeline_stages_extract_params,
    evidence_mapper=map_get_jenkins_pipeline_stages,
)
def get_jenkins_pipeline_stages(
    job_name: str,
    build_number: int,
    jenkins_url: str | None = None,
    jenkins_user: str | None = None,
    jenkins_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List the pipeline stages of a Jenkins build."""
    client = _resolve_client(jenkins_url, jenkins_user, jenkins_token)
    if client is None:
        return _not_configured("stages")
    with client:
        result = client.get_pipeline_stages(job_name, build_number)
    if not result.get("success"):
        return tool_unavailable("jenkins", result.get("error", "unknown error"), stages=[])
    return {
        "source": "jenkins",
        "available": True,
        "job": result.get("job", job_name),
        "build_number": result.get("build_number", build_number),
        "is_pipeline": result.get("is_pipeline", False),
        "status": result.get("status", ""),
        "stages": result.get("stages", []),
    }


# ---------------------------------------------------------------------------
# list_jenkins_jobs
# ---------------------------------------------------------------------------


def _list_jenkins_jobs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    jk = sources.get("jenkins", {})
    return {**_jenkins_creds(jk)}


@tool(
    name="list_jenkins_jobs",
    source="jenkins",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description="List Jenkins jobs with their last-build status.",
    use_cases=[
        "Discovering which jobs exist when the failing job name is unknown",
        "Getting an overview of which pipelines are passing or failing",
    ],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "jenkins_url": {"type": "string"},
            "jenkins_user": {"type": "string"},
            "jenkins_token": {"type": "string"},
        },
    },
    outputs={"jobs": "Jobs with name, url, status, and last-build info"},
    is_available=_jenkins_available,
    extract_params=_list_jenkins_jobs_extract_params,
    evidence_mapper=map_list_jenkins_jobs,
)
def list_jenkins_jobs(
    jenkins_url: str | None = None,
    jenkins_user: str | None = None,
    jenkins_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List Jenkins jobs with last-build status."""
    client = _resolve_client(jenkins_url, jenkins_user, jenkins_token)
    if client is None:
        return _not_configured("jobs")
    with client:
        result = client.list_jobs()
    if not result.get("success"):
        return tool_unavailable("jenkins", result.get("error", "unknown error"), jobs=[])
    return {
        "source": "jenkins",
        "available": True,
        "jobs": result.get("jobs", []),
        "total": result.get("total", 0),
        "truncated": result.get("truncated", False),
    }


# ---------------------------------------------------------------------------
# list_jenkins_running_builds
# ---------------------------------------------------------------------------


def _list_jenkins_running_builds_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    jk = sources.get("jenkins", {})
    return {**_jenkins_creds(jk)}


@tool(
    name="list_jenkins_running_builds",
    source="jenkins",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description="List Jenkins builds currently in progress across all jobs.",
    use_cases=[
        "Checking whether a build is running right now during an active incident",
        "Spotting a long-running or stuck build that may be causing impact",
    ],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "jenkins_url": {"type": "string"},
            "jenkins_user": {"type": "string"},
            "jenkins_token": {"type": "string"},
        },
    },
    outputs={"running_builds": "Builds currently in progress with job, number, and url"},
    is_available=_jenkins_available,
    extract_params=_list_jenkins_running_builds_extract_params,
    evidence_mapper=map_list_jenkins_running_builds,
)
def list_jenkins_running_builds(
    jenkins_url: str | None = None,
    jenkins_user: str | None = None,
    jenkins_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List currently running Jenkins builds."""
    client = _resolve_client(jenkins_url, jenkins_user, jenkins_token)
    if client is None:
        return _not_configured("running_builds")
    with client:
        result = client.list_running_builds()
    if not result.get("success"):
        return tool_unavailable("jenkins", result.get("error", "unknown error"), running_builds=[])
    return {
        "source": "jenkins",
        "available": True,
        "running_builds": result.get("running_builds", []),
        "total": result.get("total", 0),
        "truncated": result.get("truncated", False),
    }
