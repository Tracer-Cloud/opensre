"""Alertmanager integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.alertmanager.client import make_alertmanager_client
from integrations.alertmanager.setup import ALERTMANAGER_SETUP
from integrations.config_models import AlertmanagerIntegrationConfig

logger = logging.getLogger(__name__)


__all__ = ["ALERTMANAGER_SETUP", "classify", "make_alertmanager_client"]


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[AlertmanagerIntegrationConfig | None, str | None]:
    try:
        cfg = AlertmanagerIntegrationConfig.model_validate(
            {
                "base_url": credentials.get("base_url", ""),
                "bearer_token": credentials.get("bearer_token", ""),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="alertmanager", record_id=record_id)
        return None, None
    if cfg.base_url:
        return cfg, "alertmanager"
    return None, None
