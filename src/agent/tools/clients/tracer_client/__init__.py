"""Unified Tracer API client module."""

import base64
import json
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

_tracer_client: TracerClient | None = None


def _extract_org_id_from_jwt(jwt_token: str) -> str | None:
    """Extract organization ID from JWT token."""
    try:
        parts = jwt_token.split(".")
        if len(parts) < 2:
            return None
        # Decode JWT payload (add padding if needed)
        payload_b64 = parts[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)  # Add padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("organization")
    except Exception:
        return None


def get_tracer_client() -> TracerClient:
    """
    Get unified Tracer client singleton.

    Uses TRACER_API_URL or TRACER_WEB_APP_URL (defaults to staging.tracer.cloud).
    Requires JWT_TOKEN. TRACER_ORG_ID can be extracted from JWT if not set.
    """
    global _tracer_client

    if _tracer_client is None:
        jwt_token = os.getenv("JWT_TOKEN")
        if not jwt_token:
            raise ValueError("JWT_TOKEN environment variable is required")

        # Try to get org_id from env, or extract from JWT
        org_id = os.getenv("TRACER_ORG_ID")
        if not org_id:
            org_id = _extract_org_id_from_jwt(jwt_token)
        if not org_id:
            raise ValueError(
                "TRACER_ORG_ID environment variable is required "
                "(could not extract from JWT token)"
            )

        # Prefer TRACER_WEB_APP_URL for web app API, fallback to TRACER_API_URL for staging API
        base_url = os.getenv("TRACER_WEB_APP_URL") or os.getenv(
            "TRACER_API_URL", "https://staging.tracer.cloud"
        )
        _tracer_client = TracerClient(base_url, org_id, jwt_token)

    return _tracer_client


def get_tracer_web_client() -> TracerClient:
    """
    Alias for get_tracer_client() for backward compatibility.

    The unified client supports both staging API and web app API.
    """
    return get_tracer_client()
