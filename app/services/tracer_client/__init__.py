"""Unified Tracer API client module."""

import os

from app.auth.jwt_auth import extract_org_id_from_jwt
from app.config import get_tracer_base_url
from app.services.tracer_client.aws_batch_jobs import AWSBatchJobResult
from app.services.tracer_client.client import TracerClient
from app.services.tracer_client.tracer_integrations import (
    GrafanaIntegrationCredentials,
)
from app.services.tracer_client.tracer_logs import LogResult
from app.services.tracer_client.tracer_pipelines import (
    PipelineRunSummary,
    PipelineSummary,
    TracerRunResult,
)
from app.services.tracer_client.tracer_tools import TracerTaskResult

__all__ = [
    "AWSBatchJobResult",
    "GrafanaIntegrationCredentials",
    "LogResult",
    "PipelineRunSummary",
    "PipelineSummary",
    "TracerClient",
    "TracerRunResult",
    "TracerTaskResult",
    "get_tracer_client",
    "get_tracer_client_for_org",
    "get_tracer_web_client",
]

_tracer_client: TracerClient | None = None


def _clean_jwt(raw: str) -> str:
    token = raw.strip()
    if token.lower().startswith("bearer "):
        token = token.split(None, 1)[1].strip()
    return "".join(token.split())


def get_tracer_client() -> TracerClient:
    """Get unified Tracer client singleton. Extracts org_id from JWT."""
    global _tracer_client
    if _tracer_client is None:
        jwt_token = _clean_jwt(os.getenv("JWT_TOKEN", ""))
        if not jwt_token:
            raise ValueError("JWT_TOKEN environment variable is required")
        org_id = extract_org_id_from_jwt(jwt_token)
        if not org_id:
            raise ValueError("JWT_TOKEN must contain organization claim")
        _tracer_client = TracerClient(get_tracer_base_url(), org_id, jwt_token)
    return _tracer_client


def get_tracer_web_client() -> TracerClient:
    """Alias for get_tracer_client()."""
    return get_tracer_client()


def get_tracer_client_for_org(org_id: str, auth_token: str) -> TracerClient:
    """Create a TracerClient for a specific org using the user's auth token.

    Unlike get_tracer_client() which uses the JWT_TOKEN env var,
    this creates a client using the per-request auth token from state.

    Args:
        org_id: Organization ID from the authenticated user.
        auth_token: Raw JWT token from state._auth_token.

    Returns:
        TracerClient configured for the user's org.
    """
    token = _clean_jwt(auth_token)
    return TracerClient(get_tracer_base_url(), org_id, token)
