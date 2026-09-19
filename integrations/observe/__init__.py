"""Observe (observe.inc) integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import ObserveIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[ObserveIntegrationConfig | None, str | None]:
    try:
        cfg = ObserveIntegrationConfig.model_validate(
            {
                "customer_id": credentials.get("customer_id", ""),
                "api_token": credentials.get("api_token", ""),
                "base_url": credentials.get("base_url") or "",
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="observe", record_id=record_id)
        return None, None
    if cfg.customer_id and cfg.api_token:
        return cfg, "observe"
    return None, None
