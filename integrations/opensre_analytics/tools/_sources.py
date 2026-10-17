"""The verified analytics connection as tools receive it from integration sources."""

from __future__ import annotations

from typing import Any

from integrations.opensre_analytics.config import (
    SERVICE,
    OpenSREAnalyticsConfig,
    build_opensre_analytics_config,
)

#: Injected from the verified integration and the gateway's chat stamp, and hidden
#: from the model: a guessed URL or token can never override the configured one,
#: and the model cannot claim the conversation is somewhere it is not.
INJECTED_PARAMS = ("analytics_url", "analytics_token", "origin_platform", "origin_chat_id")


def analytics_available(sources: dict[str, Any]) -> bool:
    entry = sources.get(SERVICE)
    return isinstance(entry, dict) and bool(entry.get("connection_verified"))


def analytics_params(sources: dict[str, Any]) -> dict[str, Any]:
    entry = sources.get(SERVICE)
    if not isinstance(entry, dict):
        return {}
    return {
        "analytics_url": str(entry.get("url") or ""),
        "analytics_token": str(entry.get("auth_token") or ""),
        **origin_params(sources),
    }


def origin_params(sources: dict[str, Any]) -> dict[str, str]:
    """The chat this turn came from, stamped by the gateway; empty off chat."""
    return {
        "origin_platform": str(sources.get("_gateway_platform") or "").strip().lower(),
        "origin_chat_id": str(sources.get("_gateway_chat_id") or "").strip(),
    }


def config_from_params(
    analytics_url: str | None, analytics_token: str | None
) -> OpenSREAnalyticsConfig:
    return build_opensre_analytics_config(
        {"url": analytics_url or "", "auth_token": analytics_token or ""}
    )
