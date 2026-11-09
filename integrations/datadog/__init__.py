"""Datadog integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import DatadogIntegrationConfig
from integrations.datadog.setup import DATADOG_SETUP

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[DatadogIntegrationConfig | None, str | None]:
    try:
        cfg = DatadogIntegrationConfig.model_validate(
            {
                "api_key": credentials.get("api_key", ""),
                "app_key": credentials.get("app_key", ""),
                "site": credentials.get("site", "datadoghq.com"),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="datadog", record_id=record_id)
        return None, None
    if cfg.api_key and cfg.app_key:
        return cfg, "datadog"
    return None, None


__all__ = ["DATADOG_SETUP", "classify"]
