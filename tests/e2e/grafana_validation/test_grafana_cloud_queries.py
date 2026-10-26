import pytest
from _pytest.outcomes import Skipped

from integrations.grafana.base import _is_transport_failure
from integrations.grafana.client import get_grafana_client
from tests.e2e.grafana_validation.env_requirements import require_grafana_query_env


def _grafana_client_or_skip():
    """Build a live client, or skip when Grafana is unreachable (CI flake).

    ``get_grafana_client`` discovers datasources and re-raises transport
    timeouts so the product circuit breaker can trip. Live validation must
    treat the same failures as skips, not ERROR.
    """
    require_grafana_query_env()
    try:
        client = get_grafana_client()
    except Exception as exc:
        if _is_transport_failure(exc):
            pytest.skip(f"Grafana live query hit a transient network failure: {exc}")
        raise
    if not client.is_configured:
        pytest.skip(
            "Grafana client not configured (set GRAFANA_READ_TOKEN and GRAFANA_INSTANCE_URL if needed)"
        )
    return client


@pytest.fixture(scope="session")
def grafana_client():
    return _grafana_client_or_skip()


def _assert_query_success_or_skip_auth(result: dict) -> None:
    if result.get("success"):
        return

    detail = f"{result.get('error') or ''} {result.get('response') or ''}".strip()
    detail_lower = detail.lower()
    if any(token in detail_lower for token in ("401", "403", "unauthorized", "forbidden")):
        pytest.skip(f"Grafana live query credentials were rejected: {detail}")
    transient_network_tokens = ("timed out", "timeout", "connection reset", "failed: 499")
    if any(token in detail_lower for token in transient_network_tokens):
        pytest.skip(f"Grafana live query hit a transient network failure: {detail}")
    tempo_transient_backend_tokens = (
        "too many unhealthy instances in the ring",
        "live-store is starting",
    )
    if any(token in detail_lower for token in tempo_transient_backend_tokens):
        pytest.skip(f"Grafana Tempo live query hit a transient backend failure: {detail}")
    if "unable to find datasource" in detail_lower:
        pytest.skip(
            f"Grafana datasource UID not available (discovery may have timed out): {detail}"
        )

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


def test_assert_query_success_or_skip_auth_skips_client_closed_request():
    # Arrange: Grafana's proxy answers 499 when Loki drops the request before replying
    result = {"success": False, "error": "Loki query failed: 499"}

    # Act / Assert
    with pytest.raises(Skipped, match="transient network failure"):
        _assert_query_success_or_skip_auth(result)


def test_grafana_client_or_skip_skips_read_timeout_during_build(monkeypatch):
    import requests

    monkeypatch.setattr(
        "tests.e2e.grafana_validation.test_grafana_cloud_queries.require_grafana_query_env",
        lambda: None,
    )

    def _boom() -> object:
        raise requests.exceptions.ReadTimeout(
            "HTTPSConnectionPool(host='tracerbio.grafana.net', port=443): "
            "Read timed out. (read timeout=10)"
        )

    monkeypatch.setattr(
        "tests.e2e.grafana_validation.test_grafana_cloud_queries.get_grafana_client",
        _boom,
    )
    with pytest.raises(Skipped, match="transient network failure"):
        _grafana_client_or_skip()


def test_assert_query_success_or_skip_auth_skips_tempo_ring_failure():
    with pytest.raises(Skipped, match="transient backend failure"):
        _assert_query_success_or_skip_auth(
            {
                "success": False,
                "error": (
                    "Tempo query failed: 500 error querying live-stores in Querier.SearchRecent: "
                    "error finding partition ring replicas: partition 46: "
                    "too many unhealthy instances in the ring"
                ),
            }
        )


def test_assert_query_success_or_skip_auth_skips_tempo_live_store_starting():
    with pytest.raises(Skipped, match="transient backend failure"):
        _assert_query_success_or_skip_auth(
            {
                "success": False,
                "error": (
                    "Tempo query failed: 500 error querying live-stores in "
                    "Querier.SearchRecent: rpc error: code = Unknown desc = live-store is starting"
                ),
            }
        )


def test_assert_query_success_or_skip_auth_skips_datasource_not_found():
    with pytest.raises(Skipped, match="datasource UID not available"):
        _assert_query_success_or_skip_auth(
            {
                "success": False,
                "error": '404 {"message":"Unable to find datasource","traceID":"abc123"}',
            }
        )
