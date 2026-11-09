"""Client-backed validators for log/metric/trace backends."""

from __future__ import annotations

from integrations.config_models import (
    GrafanaIntegrationConfig,
)
from integrations.elasticsearch import ElasticsearchClient, ElasticsearchConfig
from integrations.grafana import get_grafana_client_from_credentials
from integrations.splunk import SplunkClient, SplunkConfig

from .shared import IntegrationHealthResult


def validate_grafana_integration(
    *,
    endpoint: str,
    api_key: str,
    verify_ssl: bool = True,
    ca_bundle: str = "",
) -> IntegrationHealthResult:
    """Validate Grafana credentials by discovering datasource UIDs."""
    try:
        grafana_config = GrafanaIntegrationConfig.model_validate(
            {
                "endpoint": endpoint,
                "api_key": api_key,
                "verify_ssl": verify_ssl,
                "ca_bundle": ca_bundle,
            }
        )
        client = get_grafana_client_from_credentials(
            endpoint=grafana_config.endpoint,
            api_key=grafana_config.api_key,
            account_id="opensre_onboard_probe",
            verify_ssl=grafana_config.verify_ssl,
            ca_bundle=grafana_config.ca_bundle,
        )
        discovered = client.discover_datasource_uids()
        if not discovered:
            return IntegrationHealthResult(
                ok=False,
                detail="Grafana is reachable, but no datasources could be discovered with this token.",
            )

        available = ", ".join(sorted(discovered))
        return IntegrationHealthResult(
            ok=True,
            detail=f"Grafana validated with datasource discovery: {available}.",
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"Grafana validation failed: {err}")


def validate_splunk_integration(
    *,
    base_url: str,
    token: str,
    index: str = "main",
    verify_ssl: bool = True,
    ca_bundle: str = "",
) -> IntegrationHealthResult:
    """Validate Splunk credentials by calling the server info endpoint."""
    client = SplunkClient(
        SplunkConfig(
            base_url=base_url,
            token=token,
            index=index,
            verify_ssl=verify_ssl,
            ca_bundle=ca_bundle,
        )
    )
    result = client.validate_access()
    if result.get("success"):
        return IntegrationHealthResult(ok=True, detail=result.get("detail", "Splunk connected."))
    return IntegrationHealthResult(
        ok=False,
        detail=f"Splunk validation failed: {result.get('error', 'unknown error')}",
    )


def validate_opensearch_integration(
    *,
    url: str,
    api_key: str = "",
    username: str = "",
    password: str = "",
) -> IntegrationHealthResult:
    """Validate OpenSearch / Elasticsearch connectivity via GET /_cluster/health.

    Supports three authentication modes:
    - No authentication (security disabled clusters)
    - API key (native to Elasticsearch and some OpenSearch deployments)
    - HTTP Basic Auth (default for most self-hosted OpenSearch clusters)
    """
    if not url:
        return IntegrationHealthResult(ok=False, detail="OpenSearch URL is required.")
    config = ElasticsearchConfig(
        url=url,
        api_key=api_key or None,
        username=username or None,
        password=password or None,
    )
    client = ElasticsearchClient(config)
    result = client.get_cluster_health()
    if result.get("success"):
        cluster_name = result.get("cluster_name") or "unknown"
        cluster_status = result.get("status") or "unknown"
        node_count = result.get("number_of_nodes", 0)
        return IntegrationHealthResult(
            ok=True,
            detail=(
                f"Connected to OpenSearch cluster '{cluster_name}' "
                f"({cluster_status}, {node_count} node(s))."
            ),
        )
    return IntegrationHealthResult(
        ok=False,
        detail=f"OpenSearch validation failed: {result.get('error', 'unknown error')}",
    )
