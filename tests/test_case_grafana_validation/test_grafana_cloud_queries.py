import pytest

from app.agent.tools.clients.grafana import get_grafana_client
from tests.test_case_grafana_validation.env_requirements import require_grafana_query_env


@pytest.fixture(scope="session")
def grafana_client():
    require_grafana_query_env()
    client = get_grafana_client()
    if not client.is_configured:
        pytest.skip(
            "Grafana client not configured (set GRAFANA_READ_TOKEN and GRAFANA_INSTANCE_URL if needed)"
        )
    return client


def test_grafana_logs_query(grafana_client):
    result = grafana_client.query_loki('{service_name=~".+"}', time_range_minutes=10, limit=1)
    assert result.get("success"), result.get("error") or result.get("response", "")


def test_grafana_metrics_query(grafana_client):
    result = grafana_client.query_mimir("vector(1)")
    assert result.get("success"), result.get("error") or result.get("response", "")


def test_grafana_traces_query(grafana_client):
    result = grafana_client.query_tempo("grafana-smoke-test", limit=1)
    assert result.get("success"), result.get("error") or result.get("response", "")
