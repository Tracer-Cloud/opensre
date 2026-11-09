"""Groundcover integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.groundcover.config import GroundcoverIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[GroundcoverIntegrationConfig | None, str | None]:
    try:
        cfg = GroundcoverIntegrationConfig.model_validate(
            {
                "api_key": credentials.get("api_key", "") or credentials.get("mcp_token", ""),
                "mcp_url": credentials.get("mcp_url", ""),
                "tenant_uuid": credentials.get("tenant_uuid", ""),
                "backend_id": credentials.get("backend_id", ""),
                "timezone": credentials.get("timezone", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="groundcover", record_id=record_id)
        return None, None
    if cfg.api_key:
        return cfg, "groundcover"
    return None, None
