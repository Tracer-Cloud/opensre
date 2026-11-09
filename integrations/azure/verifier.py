"""Azure Log Analytics integration verifier — config presence check only."""

from __future__ import annotations

from typing import Any

from config.constants.azure import AZURE_LOG_ANALYTICS_DEFAULT_ENDPOINT
from integrations.verification import register_verifier, result


@register_verifier("azure")
def verify_azure(source: str, config: dict[str, Any]) -> dict[str, str]:
    workspace_id = str(config.get("workspace_id", "")).strip()
    access_token = str(config.get("access_token", "")).strip()
    endpoint = str(config.get("endpoint", AZURE_LOG_ANALYTICS_DEFAULT_ENDPOINT)).strip() or (
        AZURE_LOG_ANALYTICS_DEFAULT_ENDPOINT
    )
    if not workspace_id or not access_token:
        return result(
            "azure",
            source,
            "missing",
            "Missing workspace_id or access_token.",
        )
    return result(
        "azure",
        source,
        "passed",
        f"Configured for Azure Log Analytics workspace {workspace_id} via {endpoint}.",
    )
