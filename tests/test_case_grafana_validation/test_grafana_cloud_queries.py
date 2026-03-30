import pytest
from _pytest.outcomes import Skipped

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


def _assert_query_success_or_skip_auth(result: dict) -> None:
    if result.get("success"):
        return

    detail = f"{result.get('error') or ''} {result.get('response') or ''}".strip()
    detail_lower = detail.lower()
    if any(token in detail_lower for token in ("401", "403", "unauthorized", "forbidden")):
        pytest.skip(f"Grafana live query credentials were rejected: {detail}")
    if any(token in detail_lower for token in ("timed out", "timeout", "connection reset")):
        pytest.skip(f"Grafana live query hit a transient network failure: {detail}")

    pytest.fail(detail or "Grafana query failed")


def test_grafana_logs_query(grafana_client):
    result = grafana_client.query_loki('{service_name=~".+"}', time_range_minutes=10, limit=1)
    _assert_query_success_or_skip_auth(result)


def test_grafana_metrics_query(grafana_client):
    result = grafana_client.query_mimir("vector(1)")
    _assert_query_success_or_skip_auth(result)


def test_grafana_traces_query(grafana_client):
    result = grafana_client.query_tempo("grafana-smoke-test", limit=1)
    _assert_query_success_or_skip_auth(result)


def test_assert_query_success_or_skip_auth_skips_unauthorized():
    with pytest.raises(Skipped, match="credentials were rejected"):
        _assert_query_success_or_skip_auth({"success": False, "error": "403 Forbidden"})


def test_assert_query_success_or_skip_auth_skips_timeout():
    with pytest.raises(Skipped, match="transient network failure"):
        _assert_query_success_or_skip_auth(
            {
                "success": False,
                "error": (
                    "HTTPSConnectionPool(host='tracerbio.grafana.net', port=443): "
                    "Read timed out. (read timeout=10)"
                ),
            }
        )
