"""PagerDuty integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import PagerDutyIntegrationConfig
from integrations.pagerduty.setup import PAGERDUTY_SETUP

logger = logging.getLogger(__name__)


__all__ = ["PAGERDUTY_SETUP", "classify"]


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[PagerDutyIntegrationConfig | None, str | None]:
    try:
        raw: dict[str, Any] = {
            "api_key": credentials.get("api_key", ""),
            "integration_id": record_id,
        }
        if credentials.get("base_url"):
            raw["base_url"] = credentials["base_url"]
        cfg = PagerDutyIntegrationConfig.model_validate(raw)
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="pagerduty", record_id=record_id)
        return None, None
    if cfg.api_key:
        return cfg, "pagerduty"
    return None, None
