"""Grafana Cloud client module.

Provides a unified client for querying Grafana Cloud Loki, Tempo, and Mimir.
"""

from app.agent.tools.clients.grafana.client import GrafanaClient
from app.agent.tools.clients.grafana.config import (
    GrafanaAccountConfig,
    GrafanaConfigLoader,
    get_grafana_config,
    list_grafana_accounts,
)

__all__ = [
    "GrafanaAccountConfig",
    "GrafanaClient",
    "GrafanaConfigLoader",
    "get_grafana_client",
    "get_grafana_config",
    "list_grafana_accounts",
]

_grafana_client_cache: dict[str, GrafanaClient] = {}


def get_grafana_client(account_id: str | None = None) -> GrafanaClient:
    """Get Grafana client for a specific account.

    Args:
        account_id: Grafana account identifier. If None, uses default account.

    Returns:
        GrafanaClient configured for the requested account
    """
    config = get_grafana_config(account_id)
    cache_key = config.account_id

    if cache_key not in _grafana_client_cache:
        _grafana_client_cache[cache_key] = GrafanaClient(config=config)

    return _grafana_client_cache[cache_key]
