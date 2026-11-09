"""Shared Supabase integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for Supabase projects. Covers the PostgREST API, Auth service, and
Storage service. All operations are production-safe: read-only, timeouts
enforced, result sizes capped.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any
from urllib.parse import urlparse

from pydantic import Field, field_validator

from config.strict_config import StrictConfigModel
from core.tool_framework.utils import tool_unavailable
from integrations._validation_helpers import report_classify_failure

logger = logging.getLogger(__name__)

DEFAULT_SUPABASE_TIMEOUT_SECONDS = 10.0
DEFAULT_SUPABASE_MAX_RESULTS = 50


class SupabaseConfig(StrictConfigModel):
    """Normalized Supabase connection settings."""

    url: str = ""
    service_key: str = ""
    timeout_seconds: float = Field(default=DEFAULT_SUPABASE_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_SUPABASE_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_url(cls, value: Any) -> str:  # type: ignore[override]
        return str(value or "").strip().rstrip("/")

    @field_validator("service_key", mode="before")
    @classmethod
    def _normalize_service_key(cls, value: Any) -> str:  # type: ignore[override]
        return str(value or "").strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.service_key)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.service_key}",
            "apikey": self.service_key,
            "Content-Type": "application/json",
        }


@dataclass(frozen=True)
class SupabaseValidationResult:
    """Result of validating a Supabase integration."""

    ok: bool
    detail: str


def build_supabase_config(raw: dict[str, Any] | None) -> SupabaseConfig:
    """Build a normalized Supabase config object from raw data."""
    return SupabaseConfig.model_validate(raw or {})


def supabase_config_from_env() -> SupabaseConfig | None:
    """Load a Supabase config from environment variables."""
    url = os.getenv("SUPABASE_URL", "").strip()
    service_key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not service_key:
        return None
    return build_supabase_config({"url": url, "service_key": service_key})


def _same_origin(url_a: str, url_b: str) -> bool:
    """Return True when both URLs share the same scheme and host."""
    a, b = urlparse(url_a), urlparse(url_b)
    return a.scheme == b.scheme and a.netloc == b.netloc


def resolve_supabase_config(project_url: str) -> SupabaseConfig:
    """Build a config for the given project URL, resolving credentials from
    the integration store (UI-registered) or environment variables.

    The LLM supplies only the identifying param (project_url).
    Credentials are resolved internally and never appear in tool signatures.

    Raises ValueError if no matching credentials are found for the given URL,
    or if the URL origin doesn't match any configured Supabase integration.
    """
    normalized = project_url.rstrip("/")

    # Check the integration store first — covers users who registered via the UI
    # wizard without setting environment variables (including v2 ``instances`` shape).
    try:
        from integrations.store import _record_with_flat_credentials_view, load_integrations

        for raw in load_integrations():
            record = _record_with_flat_credentials_view(raw)
            if str(record.get("service", "")).lower() != "supabase":
                continue
            creds = record.get("credentials", {}) or {}
            stored_url = str(creds.get("url", "")).rstrip("/")
            if _same_origin(stored_url, normalized):
                service_key = str(creds.get("service_key", "")).strip()
                if service_key:
                    return build_supabase_config({"url": normalized, "service_key": service_key})
    except Exception:
        logger.debug(
            "Supabase credential store lookup failed; falling back to environment",
            exc_info=True,
        )

    # Fall back to environment variables.
    env_config = supabase_config_from_env()
    if env_config is None:
        raise ValueError(
            "Supabase is not configured. "
            "Register the integration via the UI or set SUPABASE_URL and SUPABASE_SERVICE_KEY."
        )
    if not _same_origin(env_config.url, normalized):
        raise ValueError(
            f"project_url '{normalized}' does not match the configured "
            f"SUPABASE_URL origin. Refusing to attach credentials to an "
            f"unrecognised host."
        )
    return build_supabase_config({"url": normalized, "service_key": env_config.service_key})


def _make_request(
    config: SupabaseConfig,
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """Make a GET request to the Supabase project API.

    Returns (status_code, response_body). Caller handles error inspection.
    """
    from integrations.supabase.client import (
        supabase_http_get,  # lazy import to avoid circular dependency
    )

    return supabase_http_get(
        config.url,
        path,
        config.headers,
        timeout_seconds=config.timeout_seconds,
        params=params,
    )


def validate_supabase_config(config: SupabaseConfig) -> SupabaseValidationResult:
    """Validate Supabase connectivity by probing the PostgREST root endpoint."""
    if not config.url:
        return SupabaseValidationResult(ok=False, detail="Supabase URL is required.")
    if not config.service_key:
        return SupabaseValidationResult(ok=False, detail="Supabase service key is required.")

    try:
        status, _ = _make_request(config, "/rest/v1/")
        if status == HTTPStatus.OK:
            return SupabaseValidationResult(
                ok=True,
                detail=f"Connected to Supabase project at {config.url}.",
            )
        return SupabaseValidationResult(
            ok=False,
            detail=f"Supabase PostgREST returned HTTP {status}.",
        )
    except Exception as err:
        return SupabaseValidationResult(ok=False, detail=f"Supabase connection failed: {err}")


def supabase_is_available(sources: dict[str, dict]) -> bool:  # type: ignore[type-arg]
    """Check if Supabase integration identifying params are present."""
    sb = sources.get("supabase", {})
    return bool(sb.get("project_url"))


def supabase_extract_params(sources: dict[str, dict]) -> dict[str, Any]:  # type: ignore[type-arg]
    """Extract Supabase identifying params from resolved integrations.

    The service key is resolved internally (integration store or environment)
    so it never appears in tool signatures and is never seen by the LLM.
    """
    sb = sources.get("supabase", {})
    return {
        "project_url": str(sb.get("project_url", "")).strip(),
    }


def get_service_health(config: SupabaseConfig) -> dict[str, Any]:
    """Check the health of all Supabase services: PostgREST, Auth, and Storage.

    Read-only: hits dedicated health endpoints only. Returns a per-service
    breakdown so the agent can pinpoint which layer is degraded.
    """
    if not config.is_configured:
        return tool_unavailable("supabase", "Not configured.")

    services: dict[str, Any] = {}

    # PostgREST — the database REST API layer
    try:
        status, _ = _make_request(config, "/rest/v1/")
        services["postgrest"] = {
            "healthy": status == HTTPStatus.OK,
            "status_code": status,
        }
    except Exception as err:
        services["postgrest"] = {"healthy": False, "error": str(err)}

    # Auth service
    try:
        status, body = _make_request(config, "/auth/v1/health")
        detail = ""
        if isinstance(body, dict):
            detail = body.get("description", "")
        elif isinstance(body, str):
            detail = body
        services["auth"] = {
            "healthy": status == HTTPStatus.OK,
            "status_code": status,
            "detail": detail,
        }
    except Exception as err:
        services["auth"] = {"healthy": False, "error": str(err)}

    # Storage service — dedicated health endpoint; does not require bucket permissions
    try:
        status, _ = _make_request(config, "/storage/v1/health")
        services["storage"] = {
            "healthy": status == HTTPStatus.OK,
            "status_code": status,
        }
    except Exception as err:
        services["storage"] = {"healthy": False, "error": str(err)}

    all_healthy = all(s.get("healthy", False) for s in services.values())
    degraded = [name for name, s in services.items() if not s.get("healthy", False)]

    return {
        "source": "supabase",
        "available": True,
        "project_url": config.url,
        "overall_healthy": all_healthy,
        "degraded_services": degraded,
        "services": services,
    }


def get_storage_buckets(config: SupabaseConfig) -> dict[str, Any]:
    """Retrieve all storage buckets and their basic metadata.

    Read-only: queries the Supabase Storage API. Useful for detecting
    misconfigured or unexpectedly missing buckets during a file upload incident.
    Results are capped at config.max_results.
    """
    if not config.is_configured:
        return tool_unavailable("supabase", "Not configured.")

    try:
        status, body = _make_request(config, "/storage/v1/bucket")

        if status != HTTPStatus.OK:
            return tool_unavailable("supabase", f"Storage API returned HTTP {status}.")

        raw_buckets: list[dict[str, Any]] = body if isinstance(body, list) else []
        actual_total = len(raw_buckets)
        bucket_summaries = []
        for bucket in raw_buckets[: config.max_results]:
            bucket_summaries.append(
                {
                    "id": bucket.get("id", ""),
                    "name": bucket.get("name", ""),
                    "public": bucket.get("public", False),
                    "file_size_limit": bucket.get("file_size_limit"),
                    "allowed_mime_types": bucket.get("allowed_mime_types"),
                    "created_at": bucket.get("created_at", ""),
                    "updated_at": bucket.get("updated_at", ""),
                }
            )
        returned = len(bucket_summaries)

        return {
            "source": "supabase",
            "available": True,
            "project_url": config.url,
            "total_buckets": actual_total,
            "returned_buckets": returned,
            "truncated": actual_total > returned,
            "buckets": bucket_summaries,
        }
    except Exception as err:
        return tool_unavailable("supabase", str(err))


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        cfg = build_supabase_config(
            {
                "url": credentials.get("url", ""),
                "service_key": credentials.get("service_key", ""),
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="supabase", record_id=record_id)
        return None, None
    if cfg.is_configured:
        return {"project_url": cfg.url, "integration_id": record_id}, "supabase"
    return None, None
