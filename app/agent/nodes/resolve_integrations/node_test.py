from __future__ import annotations

from app.agent.nodes.plan_actions.detect_sources import detect_sources
from app.agent.nodes.resolve_integrations.node import (
    _classify_integrations,
    _load_env_integrations,
    _merge_integrations_by_service,
    _merge_local_integrations,
    node_resolve_integrations,
)


def test_load_env_integrations_reads_grafana_datadog_and_aws(monkeypatch) -> None:
    monkeypatch.setenv("GRAFANA_INSTANCE_URL", "https://example.grafana.net")
    monkeypatch.setenv("GRAFANA_READ_TOKEN", "grafana-token")
    monkeypatch.setenv("DD_API_KEY", "dd-api")
    monkeypatch.setenv("DD_APP_KEY", "dd-app")
    monkeypatch.setenv("DD_SITE", "datadoghq.eu")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "session")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")

    integrations = _load_env_integrations()
    by_service = {integration["service"]: integration for integration in integrations}

    assert by_service["grafana"]["credentials"]["endpoint"] == "https://example.grafana.net"
    assert by_service["datadog"]["credentials"]["site"] == "datadoghq.eu"
    assert by_service["aws"]["credentials"]["access_key_id"] == "AKIA_TEST"
    assert by_service["aws"]["credentials"]["region"] == "eu-west-1"


def test_merge_local_integrations_prefers_store_over_env() -> None:
    merged = _merge_local_integrations(
        store_integrations=[
            {
                "id": "store-datadog",
                "service": "datadog",
                "status": "active",
                "credentials": {"api_key": "store-api", "app_key": "store-app", "site": "datadoghq.com"},
            }
        ],
        env_integrations=[
            {
                "id": "env-datadog",
                "service": "datadog",
                "status": "active",
                "credentials": {"api_key": "env-api", "app_key": "env-app", "site": "datadoghq.eu"},
            },
            {
                "id": "env-grafana",
                "service": "grafana",
                "status": "active",
                "credentials": {"endpoint": "https://example.grafana.net", "api_key": "grafana-token"},
            },
        ],
    )

    by_service = {integration["service"]: integration for integration in merged}
    assert by_service["datadog"]["id"] == "store-datadog"
    assert by_service["grafana"]["id"] == "env-grafana"


def test_merge_integrations_by_service_prefers_later_groups() -> None:
    merged = _merge_integrations_by_service(
        [
            {
                "id": "env-datadog",
                "service": "datadog",
                "status": "active",
                "credentials": {"api_key": "env-api", "app_key": "env-app", "site": "datadoghq.eu"},
            }
        ],
        [
            {
                "id": "remote-datadog",
                "service": "datadog",
                "status": "active",
                "credentials": {"api_key": "remote-api", "app_key": "remote-app", "site": "datadoghq.com"},
            },
            {
                "id": "remote-grafana",
                "service": "grafana",
                "status": "active",
                "credentials": {"endpoint": "https://remote.grafana.net", "api_key": "remote-token"},
            },
        ],
    )

    by_service = {integration["service"]: integration for integration in merged}
    assert by_service["datadog"]["id"] == "remote-datadog"
    assert by_service["grafana"]["id"] == "remote-grafana"


def test_classify_integrations_supports_aws_credentials_mode() -> None:
    resolved = _classify_integrations([
        {
            "id": "env-aws",
            "service": "aws",
            "status": "active",
            "credentials": {
                "access_key_id": "AKIA_TEST",
                "secret_access_key": "secret",
                "session_token": "session",
                "region": "eu-west-1",
            },
        }
    ])

    assert resolved["aws"]["region"] == "eu-west-1"
    assert resolved["aws"]["credentials"]["access_key_id"] == "AKIA_TEST"


def test_node_resolve_integrations_uses_env_when_no_auth_or_store(monkeypatch) -> None:
    monkeypatch.delenv("JWT_TOKEN", raising=False)
    monkeypatch.setenv("GRAFANA_INSTANCE_URL", "https://example.grafana.net")
    monkeypatch.setenv("GRAFANA_READ_TOKEN", "grafana-token")
    monkeypatch.setenv("DD_API_KEY", "dd-api")
    monkeypatch.setenv("DD_APP_KEY", "dd-app")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    monkeypatch.setattr("app.integrations.store.load_integrations", lambda: [])

    result = node_resolve_integrations({})
    resolved = result["resolved_integrations"]

    assert resolved["grafana"]["endpoint"] == "https://example.grafana.net"
    assert resolved["datadog"]["api_key"] == "dd-api"
    assert resolved["aws"]["credentials"]["access_key_id"] == "AKIA_TEST"


def test_node_resolve_integrations_merges_remote_with_local_fallback(monkeypatch) -> None:
    class _FakeTracerClient:
        def get_all_integrations(self) -> list[dict]:
            return [
                {
                    "id": "remote-datadog",
                    "service": "datadog",
                    "status": "active",
                    "credentials": {
                        "api_key": "remote-api",
                        "app_key": "remote-app",
                        "site": "datadoghq.com",
                    },
                }
            ]

    def _fake_get_tracer_client_for_org(_org_id: str, _token: str) -> _FakeTracerClient:
        return _FakeTracerClient()

    monkeypatch.setenv("JWT_TOKEN", "header.payload.signature")
    monkeypatch.setenv("DD_API_KEY", "env-api")
    monkeypatch.setenv("DD_APP_KEY", "env-app")
    monkeypatch.setenv("GRAFANA_INSTANCE_URL", "https://local.grafana.net")
    monkeypatch.setenv("GRAFANA_READ_TOKEN", "local-token")
    monkeypatch.setattr("app.integrations.store.load_integrations", lambda: [])
    monkeypatch.setattr(
        "app.agent.tools.clients.tracer_client.get_tracer_client_for_org",
        _fake_get_tracer_client_for_org,
    )
    monkeypatch.setattr(
        "app.agent.nodes.resolve_integrations.node._decode_org_id_from_token",
        lambda _token: "org_123",
    )

    result = node_resolve_integrations({})
    resolved = result["resolved_integrations"]

    assert resolved["datadog"]["api_key"] == "remote-api"
    assert resolved["grafana"]["endpoint"] == "https://local.grafana.net"


def test_detect_sources_ignores_eks_without_role_arn() -> None:
    sources = detect_sources(
        raw_alert={
            "alert_source": "datadog",
            "eks_cluster": "demo-cluster",
            "kube_namespace": "demo",
        },
        context={},
        resolved_integrations={
            "aws": {
                "region": "us-east-1",
                "credentials": {
                    "access_key_id": "AKIA_TEST",
                    "secret_access_key": "secret",
                },
            }
        },
    )

    assert "eks" not in sources
