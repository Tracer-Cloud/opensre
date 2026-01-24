"""Unified Tracer API client module."""

import os

from src.agent.tools.clients.tracer_client.aws_batch_jobs import AWSBatchJobResult
from src.agent.tools.clients.tracer_client.client import TracerClient
from src.agent.tools.clients.tracer_client.tracer_logs import LogResult
from src.agent.tools.clients.tracer_client.tracer_pipelines import (
    PipelineRunSummary,
    PipelineSummary,
    TracerRunResult,
)
from src.agent.tools.clients.tracer_client.tracer_tools import TracerTaskResult

__all__ = [
    "AWSBatchJobResult",
    "LogResult",
    "PipelineRunSummary",
    "PipelineSummary",
    "TracerClient",
    "TracerRunResult",
    "TracerTaskResult",
    "get_tracer_client",
    "get_tracer_web_client",  # Alias for backward compatibility
]

# Hardcoded defaults for Tracer Cloud
DEFAULT_ORG_ID = "org_33W1pou1nUzYoYPZj3OCQ3jslB2"
DEFAULT_BASE_URL = "https://staging.tracer.cloud"

_tracer_client: TracerClient | None = None


def get_tracer_client() -> TracerClient:
    """
    Get unified Tracer client singleton.

    Defaults to staging.tracer.cloud. Only requires JWT_TOKEN.
    """
    global _tracer_client

    if _tracer_client is None:
        jwt_token = os.getenv("JWT_TOKEN")
        if not jwt_token:
            raise ValueError("JWT_TOKEN environment variable is required")

        org_id = os.getenv("TRACER_ORG_ID", DEFAULT_ORG_ID)
        base_url = os.getenv("TRACER_WEB_APP_URL", DEFAULT_BASE_URL)
        _tracer_client = TracerClient(base_url, org_id, jwt_token)

    return _tracer_client


def get_tracer_web_client() -> TracerClient:
    """
    Alias for get_tracer_client() for backward compatibility.

    The unified client supports both staging API and web app API.
    """
    return get_tracer_client()
