"""Honeycomb integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.honeycomb.config import HoneycombIntegrationConfig
from integrations.honeycomb.setup import HONEYCOMB_SETUP

logger = logging.getLogger(__name__)


__all__ = ["HONEYCOMB_SETUP", "classify"]


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[HoneycombIntegrationConfig | None, str | None]:
    try:
        cfg = HoneycombIntegrationConfig.model_validate(
            {
                "api_key": credentials.get("api_key", ""),
                "dataset": credentials.get("dataset", ""),
                "base_url": credentials.get("base_url", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="honeycomb", record_id=record_id)
        return None, None
    if cfg.api_key:
        return cfg, "honeycomb"
    return None, None
