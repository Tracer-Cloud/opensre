"""Splunk client factory for the SplunkSearchTool."""

from __future__ import annotations

from typing import Any

from app.services.splunk.client import SplunkClient, SplunkConfig


def make_client(
    base_url: str | None,
    token: str | None = None,
    index: str = "main",
    verify_ssl: bool = True,
    ca_bundle: str = "",
) -> SplunkClient | None:
    if not base_url or not token:
        return None
    return SplunkClient(
        SplunkConfig(
            base_url=base_url,
            token=token,
            index=index,
            verify_ssl=verify_ssl,
            ca_bundle=ca_bundle,
        )
    )


def unavailable(source: str, empty_key: str, error: str, **extra: Any) -> dict[str, Any]:
    """Standardised unavailable response."""
    return {"source": source, "available": False, "error": error, empty_key: [], **extra}
