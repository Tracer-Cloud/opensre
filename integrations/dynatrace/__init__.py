"""Dynatrace integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.dynatrace.config import DynatraceIntegrationConfig
from integrations.dynatrace.setup import DYNATRACE_SETUP

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[DynatraceIntegrationConfig | None, str | None]:
    """Classify credentials when both tenant URL and platform token are present."""
    try:
        config = DynatraceIntegrationConfig.model_validate(
            {
                "tenant_url": credentials.get("tenant_url", ""),
                "platform_token": credentials.get("platform_token", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="dynatrace", record_id=record_id)
        return None, None
    if config.tenant_url and config.platform_token:
        return config, "dynatrace"
    return None, None


__all__ = ["DYNATRACE_SETUP", "classify"]
