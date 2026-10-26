"""Coralogix integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import CoralogixIntegrationConfig
from integrations.coralogix.setup import CORALOGIX_SETUP

logger = logging.getLogger(__name__)


__all__ = ["CORALOGIX_SETUP", "classify"]


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[CoralogixIntegrationConfig | None, str | None]:
    try:
        cfg = CoralogixIntegrationConfig.model_validate(
            {
                "api_key": credentials.get("api_key", ""),
                "base_url": credentials.get("base_url", ""),
                "application_name": credentials.get("application_name", ""),
                "subsystem_name": credentials.get("subsystem_name", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="coralogix", record_id=record_id)
        return None, None
    if cfg.api_key:
        return cfg, "coralogix"
    return None, None
