"""Vercel integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.vercel.client import VercelConfig
from integrations.vercel.setup import VERCEL_SETUP

logger = logging.getLogger(__name__)


__all__ = ["VERCEL_SETUP", "classify"]


def classify(credentials: dict[str, Any], record_id: str) -> tuple[VercelConfig | None, str | None]:
    try:
        cfg = VercelConfig.model_validate(
            {
                "api_token": credentials.get("api_token", ""),
                "team_id": credentials.get("team_id", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="vercel", record_id=record_id)
        return None, None
    if cfg.api_token:
        return cfg, "vercel"
    return None, None
