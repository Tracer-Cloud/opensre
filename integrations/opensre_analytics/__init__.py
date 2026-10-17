"""OpenSRE Analytics: the product-analytics app, read through its MCP endpoint."""

from integrations.opensre_analytics.config import (
    DEFAULT_OPENSRE_ANALYTICS_URL,
    SERVICE,
    OpenSREAnalyticsConfig,
    build_opensre_analytics_config,
    classify,
)

__all__ = [
    "DEFAULT_OPENSRE_ANALYTICS_URL",
    "SERVICE",
    "OpenSREAnalyticsConfig",
    "build_opensre_analytics_config",
    "classify",
]
