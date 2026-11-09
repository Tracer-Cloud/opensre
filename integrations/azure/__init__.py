"""Azure Log Analytics integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from config.constants.azure import (
    AZURE_LOG_ANALYTICS_DEFAULT_ENDPOINT,
    AZURE_MAX_RESULTS_DEFAULT,
)
from integrations._validation_helpers import report_classify_failure
from integrations.config_models import AzureIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[AzureIntegrationConfig | None, str | None]:
    try:
        cfg = AzureIntegrationConfig.model_validate(
            {
                "workspace_id": credentials.get("workspace_id", ""),
                "access_token": credentials.get("access_token", ""),
                "endpoint": credentials.get("endpoint", AZURE_LOG_ANALYTICS_DEFAULT_ENDPOINT),
                "tenant_id": credentials.get("tenant_id", ""),
                "subscription_id": credentials.get("subscription_id", ""),
                "max_results": credentials.get("max_results", AZURE_MAX_RESULTS_DEFAULT),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="azure", record_id=record_id)
        return None, None
    if cfg.workspace_id and cfg.access_token:
        return cfg, "azure"
    return None, None
