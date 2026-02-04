import os
from contextlib import contextmanager

from app.outbound_telemetry.grafana_client import GrafanaCloudClient


@contextmanager
def temp_env(values: dict[str, str]):
    original = os.environ.copy()
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def test_grafana_client_builders():
    with temp_env(
        {
            "GCLOUD_HOSTED_LOGS_ID": "1",
            "GCLOUD_HOSTED_LOGS_URL": "https://logs.example.com/loki/api/v1/push",
            "GCLOUD_HOSTED_METRICS_ID": "2",
            "GCLOUD_HOSTED_METRICS_URL": "https://metrics.example.com/api/prom/push",
            "GCLOUD_HOSTED_TRACES_ID": "3",
            "GCLOUD_HOSTED_TRACES_URL_TEMPO": "https://tempo.example.com/tempo",
            "GCLOUD_RW_API_KEY": "token",
        }
    ):
        client = GrafanaCloudClient.from_env()
        assert client.config.is_configured
        assert "query_range" in client._loki_query_range_url()
        assert "api/v1/query" in client._mimir_query_url()
        assert client._tempo_search_url().endswith("/api/search")
        query = client.build_logql_query("service", correlation_id="corr")
        assert 'service_name="service"' in query
        assert "corr" in query
