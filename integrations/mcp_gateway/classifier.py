"""Catalog classification for MCP gateway records."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.mcp_gateway.config import McpGatewayConfig, build_mcp_gateway_config

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[McpGatewayConfig | None, str | None]:
    """Classify one active integration record into runtime configuration."""
    try:
        config = build_mcp_gateway_config({**credentials, "integration_id": record_id})
    except Exception as exc:
        report_classify_failure(
            exc,
            logger=logger,
            integration="mcp_gateway",
            record_id=record_id,
        )
        return None, None
    if config.is_configured:
        return config, "mcp_gateway"
    return None, None
