"""New Relic integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.new_relic.config import NewRelicIntegrationConfig
from integrations.new_relic.setup import NEW_RELIC_SETUP

logger = logging.getLogger(__name__)


__all__ = ["NEW_RELIC_SETUP", "classify"]


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[NewRelicIntegrationConfig | None, str | None]:
    """Classify credentials as a New Relic integration.

    Only recognized as configured when **both** ``api_key`` and ``account_id``
    are present (FR-2) — either alone doesn't configure anything usable, since
    NerdGraph queries always target a specific account.
    """
    try:
        cfg = NewRelicIntegrationConfig.model_validate(
            {
                "api_key": credentials.get("api_key", ""),
                "account_id": credentials.get("account_id", ""),
                "base_url": credentials.get("base_url", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="new_relic", record_id=record_id)
        return None, None
    if cfg.api_key and cfg.account_id:
        return cfg, "new_relic"
    return None, None
