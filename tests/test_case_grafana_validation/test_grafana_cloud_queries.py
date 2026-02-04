import pytest

from app.agent.tools.clients.grafana import get_grafana_client


@pytest.fixture(scope="session")
def grafana_client():
    client = get_grafana_client()
    if not client.is_configured:
        pytest.skip("Grafana client not configured (check grafana_accounts.yaml and GRAFANA_READ_TOKEN)")
    return client


def test_grafana_logs_query(grafana_client):
    result = grafana_client.query_loki("{}", time_range_minutes=10, limit=1)
    assert result.get("success"), result.get("error") or result.get("response", "")


def test_grafana_metrics_query(grafana_client):
    result = grafana_client.query_mimir("1")
    assert result.get("success"), result.get("error") or result.get("response", "")


def test_grafana_traces_query(grafana_client):
    result = grafana_client.query_tempo("grafana-smoke-test", limit=1)
    assert result.get("success"), result.get("error") or result.get("response", "")
