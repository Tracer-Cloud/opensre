"""incident.io integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import IncidentIoIntegrationConfig
from integrations.incident_io.setup import INCIDENT_IO_SETUP

logger = logging.getLogger(__name__)


__all__ = ["INCIDENT_IO_SETUP", "classify"]


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[IncidentIoIntegrationConfig | None, str | None]:
    try:
        cfg = IncidentIoIntegrationConfig.model_validate(
            {
                "api_key": credentials.get("api_key", ""),
                "base_url": credentials.get("base_url", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="incident_io", record_id=record_id)
        return None, None
    if cfg.api_key:
        return cfg, "incident_io"
    return None, None
