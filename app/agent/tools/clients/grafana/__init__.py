"""Grafana Cloud client module.

Provides a unified client for querying Grafana Cloud Loki, Tempo, and Mimir.
Supports both environment-based config and dynamic credentials from the web app DB.
"""

from app.agent.tools.clients.grafana.client import GrafanaClient
from app.agent.tools.clients.grafana.config import (
    DEFAULT_LOKI_UID,
    DEFAULT_MIMIR_UID,
    DEFAULT_TEMPO_UID,
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
    "get_grafana_client_from_credentials",
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


def get_grafana_client_from_credentials(
    endpoint: str,
    api_key: str,
    account_id: str = "user_integration",
    loki_datasource_uid: str = DEFAULT_LOKI_UID,
    tempo_datasource_uid: str = DEFAULT_TEMPO_UID,
    mimir_datasource_uid: str = DEFAULT_MIMIR_UID,
) -> GrafanaClient:
    """Create a Grafana client from credentials fetched from the web app DB.

    This bypasses environment-based config and uses the user's own Grafana
    integration token stored in the integrations table.

    Args:
        endpoint: Grafana instance URL (e.g., https://myorg.grafana.net)
        api_key: Grafana API key / service account token
        account_id: Identifier for caching (default: "user_integration")
        loki_datasource_uid: Loki datasource UID (default: grafanacloud-logs)
        tempo_datasource_uid: Tempo datasource UID (default: grafanacloud-traces)
        mimir_datasource_uid: Mimir datasource UID (default: grafanacloud-prom)

    Returns:
        GrafanaClient configured with the provided credentials
    """
    config = GrafanaAccountConfig(
        account_id=account_id,
        instance_url=endpoint.rstrip("/"),
        read_token=api_key,
        loki_datasource_uid=loki_datasource_uid,
        tempo_datasource_uid=tempo_datasource_uid,
        mimir_datasource_uid=mimir_datasource_uid,
        description=f"User integration ({account_id})",
    )
    return GrafanaClient(config=config)
