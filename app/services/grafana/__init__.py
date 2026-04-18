"""Grafana Cloud client module.

Provides a unified client for querying Grafana Cloud Loki, Tempo, and Mimir.
Credentials come from the user's integration stored in the Tracer web app DB.
"""

import logging

from app.services.grafana.client import GrafanaClient
from app.services.grafana.config import GrafanaAccountConfig

logger = logging.getLogger(__name__)

__all__ = [
    "GrafanaAccountConfig",
    "GrafanaClient",
    "get_grafana_client",
    "get_grafana_client_from_credentials",
]

_grafana_client_cache: dict[str, GrafanaClient] = {}


def get_grafana_client() -> GrafanaClient:
    """Create a Grafana client from environment variables.

    Reads GRAFANA_INSTANCE_URL and GRAFANA_READ_TOKEN from env / .env file.
    Intended for local tests and demo pipelines only — production code should
    use get_grafana_client_from_credentials() with explicit credentials.
    """
    import os

    return get_grafana_client_from_credentials(
        endpoint=os.getenv("GRAFANA_INSTANCE_URL", "https://tracerbio.grafana.net"),
        api_key=os.getenv("GRAFANA_READ_TOKEN", ""),
        account_id="env_default",
    )


def get_grafana_client_from_credentials(
    endpoint: str,
    api_key: str,
    account_id: str = "user_integration",
) -> GrafanaClient:
    """Create a Grafana client from integration credentials.

    Datasource UIDs are auto-discovered from the user's Grafana instance
    via GET /api/datasources. Results are cached per endpoint.

    Args:
        endpoint: Grafana instance URL (e.g., https://myorg.grafana.net)
        api_key: Grafana service account token (glsa_...)
        account_id: Identifier for caching (default: "user_integration")

    Returns:
        GrafanaClient configured with discovered datasource UIDs
    """
    cache_key = f"creds_{account_id}_{endpoint}"
    if cache_key in _grafana_client_cache:
        return _grafana_client_cache[cache_key]

    config = GrafanaAccountConfig(
        account_id=account_id,
        instance_url=endpoint.rstrip("/"),
        read_token=api_key,
    )
    client = GrafanaClient(config=config)

    # Auto-discover actual datasource UIDs from the user's Grafana instance
    discovered = client.discover_datasource_uids()
    if discovered:
        config = GrafanaAccountConfig(
            account_id=account_id,
            instance_url=endpoint.rstrip("/"),
            read_token=api_key,
            loki_datasource_uid=discovered.get("loki_uid", ""),
            tempo_datasource_uid=discovered.get("tempo_uid", ""),
            mimir_datasource_uid=discovered.get("mimir_uid", ""),
        )
        client = GrafanaClient(config=config)
        logger.info(
            "[grafana] Client ready for account_id=%s with datasource discovery status: loki=%s tempo=%s mimir=%s",
            account_id,
            config.loki_datasource_uid,
            config.tempo_datasource_uid,
            config.mimir_datasource_uid,
        )
    else:
        logger.warning(
            "[grafana] Could not discover datasource UIDs for account_id=%s — queries will fail",
            account_id,
        )

    _grafana_client_cache[cache_key] = client
    return client
