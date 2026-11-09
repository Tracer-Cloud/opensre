"""ServiceNow integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import ServiceNowIntegrationConfig
from integrations.servicenow.setup import SERVICENOW_SETUP
from integrations.servicenow.verifier import build_servicenow_config, validate_servicenow_config

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[ServiceNowIntegrationConfig | None, str | None]:
    try:
        cfg = ServiceNowIntegrationConfig.model_validate(
            {
                "instance_url": str(credentials.get("instance_url") or "").strip()
                or str(credentials.get("url") or "").strip(),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="servicenow", record_id=record_id)
        return None, None
    if cfg.instance_url and cfg.username and cfg.password:
        return cfg, "servicenow"
    return None, None


__all__ = ["SERVICENOW_SETUP", "build_servicenow_config", "classify", "validate_servicenow_config"]
