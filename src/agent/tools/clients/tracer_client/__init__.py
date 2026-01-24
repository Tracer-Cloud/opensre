"""Unified Tracer API client module."""

import os

from src.agent.constants import TRACER_BASE_URL, TRACER_ORG_ID
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

_tracer_client: TracerClient | None = None


def get_tracer_client() -> TracerClient:
    """
    Get unified Tracer client singleton.

    Only requires JWT_TOKEN. Org ID and URL are from constants.
    """
    global _tracer_client

    if _tracer_client is None:
        raw_token = os.getenv("JWT_TOKEN", "")
        jwt_token = raw_token.strip()
        if jwt_token.lower().startswith("bearer "):
            jwt_token = jwt_token.split(None, 1)[1].strip()
        jwt_token = "".join(jwt_token.split())
        if not jwt_token:
            raise ValueError("JWT_TOKEN environment variable is required")

        _tracer_client = TracerClient(TRACER_BASE_URL, TRACER_ORG_ID, jwt_token)

    return _tracer_client


def get_tracer_web_client() -> TracerClient:
    """
    Alias for get_tracer_client() for backward compatibility.

    The unified client supports both staging API and web app API.
    """
    return get_tracer_client()
