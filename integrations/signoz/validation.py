"""SigNoz connectivity validation."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from integrations._validation_helpers import report_validation_failure
from integrations.signoz.config import SigNozConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SigNozValidationResult:
    """Result of validating a SigNoz integration."""

    ok: bool
    detail: str


def validate_signoz_config(config: SigNozConfig) -> SigNozValidationResult:
    """Validate SigNoz Query API connectivity."""
    if not config.is_configured:
        return SigNozValidationResult(
            ok=False,
            detail=(
                "SigNoz configuration is incomplete. "
                "Provide SIGNOZ_URL and SIGNOZ_API_KEY (service account key)."
            ),
        )

    base_url = config.url.rstrip("/")

    try:
        response = httpx.get(
            f"{base_url}/api/v2/metrics",
            headers={
                "SigNoz-Api-Key": config.api_key,
                "Accept": "application/json",
            },
            params={"limit": 1, "offset": 0},
            timeout=config.timeout_seconds,
        )
        response.raise_for_status()
        return SigNozValidationResult(
            ok=True,
            detail=(
                "Connected to SigNoz Query API "
                "(/api/v2/metrics, /api/v5/query_range for logs/metrics/traces)."
            ),
        )
    except httpx.HTTPStatusError as err:
        snippet = err.response.text[:200].strip()
        detail = (
            f"HTTP {err.response.status_code}: {snippet}"
            if snippet
            else f"HTTP {err.response.status_code}"
        )
        return SigNozValidationResult(
            ok=False,
            detail=f"SigNoz Query API validation failed: {detail}",
        )
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="signoz",
            method="validate_signoz_config",
        )
        return SigNozValidationResult(
            ok=False,
            detail=f"SigNoz Query API validation failed: {err}",
        )


__all__ = ["SigNozValidationResult", "validate_signoz_config"]
