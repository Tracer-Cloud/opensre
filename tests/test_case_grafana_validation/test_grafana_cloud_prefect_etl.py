import pytest

from tests.test_case_grafana_validation.validate_grafana_cloud import GrafanaCloud


@pytest.fixture(scope="session")
def grafana_client():
    client = GrafanaCloud()
    missing = client.missing_env()
    if missing:
        pytest.skip("Missing Grafana Cloud env vars: " + ", ".join(missing))
    return client


def test_prefect_etl_logs(grafana_client):
    ok, detail = grafana_client.check_logs()
    assert ok, detail


def test_prefect_etl_metrics(grafana_client):
    ok, detail = grafana_client.check_metrics()
    assert ok, detail


def test_prefect_etl_traces(grafana_client):
    ok, detail = grafana_client.check_traces()
    assert ok, detail
