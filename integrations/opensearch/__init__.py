"""OpenSearch integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import OpenSearchIntegrationConfig
from integrations.opensearch.setup import OPENSEARCH_SETUP

logger = logging.getLogger(__name__)


__all__ = ["OPENSEARCH_SETUP", "classify"]


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[OpenSearchIntegrationConfig | None, str | None]:
    try:
        cfg = OpenSearchIntegrationConfig.model_validate(
            {
                "url": credentials.get("url", ""),
                "api_key": credentials.get("api_key", ""),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "index_pattern": credentials.get("index_pattern", "*"),
                "max_results": credentials.get("max_results", 100),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="opensearch", record_id=record_id)
        return None, None
    if cfg.url:
        return cfg, "opensearch"
    return None, None
