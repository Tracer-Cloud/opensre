"""Catalog and verification coverage for the Argo CD integration."""

from __future__ import annotations

from typing import Any

import pytest

from app.integrations.catalog import classify_integrations, resolve_effective_integrations
from app.integrations.models import ArgoCDIntegrationConfig
from app.integrations.verify import _verify_argocd, verify_integrations


@pytest.fixture(autouse=True)
def _clear_argocd_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ARGOCD_INSTANCES",
        "ARGOCD_BASE_URL",
        "ARGOCD_AUTH_TOKEN",
        "ARGOCD_TOKEN",
        "ARGOCD_USERNAME",
        "ARGOCD_PASSWORD",
        "ARGOCD_PROJECT",
        "ARGOCD_APP_NAMESPACE",
        "ARGOCD_VERIFY_SSL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_classify_argocd_store_record() -> None:
    resolved = classify_integrations(
        [
            {
                "id": "argocd-store-1",
                "service": "argocd",
                "status": "active",
                "credentials": {
                    "base_url": "https://argocd.example.com/",
                    "bearer_token": "Bearer tok_store",
                    "project": "default",
                    "app_namespace": "argocd",
                },
            }
        ]
    )

    assert resolved["argocd"]["base_url"] == "https://argocd.example.com"
    assert resolved["argocd"]["bearer_token"] == "tok_store"
    assert resolved["argocd"]["project"] == "default"
    assert resolved["argocd"]["app_namespace"] == "argocd"
    assert resolved["argocd"]["integration_id"] == "argocd-store-1"


def test_classify_argocd_rejects_plain_http_remote() -> None:
    resolved = classify_integrations(
        [
            {
                "id": "argocd-store-plain-http",
                "service": "argocd",
                "status": "active",
                "credentials": {
                    "base_url": "http://argocd.example.com",
                    "bearer_token": "tok_store",
                },
            }
        ]
    )

    assert "argocd" not in resolved


def test_classify_argocd_rejects_ambiguous_auth_methods() -> None:
    resolved = classify_integrations(
        [
            {
                "id": "argocd-store-dual-auth",
                "service": "argocd",
                "status": "active",
                "credentials": {
                    "base_url": "https://argocd.example.com",
                    "bearer_token": "tok_store",
                    "username": "admin",
                    "password": "pw",
                },
            }
        ]
    )

    assert "argocd" not in resolved


def test_argocd_integration_config_only_strips_bearer_prefix_from_token() -> None:
    config = ArgoCDIntegrationConfig(
        base_url="https://argocd.example.com",
        bearer_token="Bearer tok_store",
        project="bearer platform",
        app_namespace="bearer namespace",
        integration_id="bearer integration",
    )

    assert config.bearer_token == "tok_store"
    assert config.project == "bearer platform"
    assert config.app_namespace == "bearer namespace"
    assert config.integration_id == "bearer integration"


def test_resolve_effective_integrations_includes_argocd_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv("ARGOCD_BASE_URL", "https://argocd.example.com/")
    monkeypatch.setenv("ARGOCD_AUTH_TOKEN", "Bearer tok_env")
    monkeypatch.setenv("ARGOCD_PROJECT", "payments")
    monkeypatch.setenv("ARGOCD_APP_NAMESPACE", "argocd")
    monkeypatch.setenv("ARGOCD_VERIFY_SSL", "false")

    effective = resolve_effective_integrations()

    argocd = effective.get("argocd")
    assert argocd is not None
    assert argocd["source"] == "local env"
    assert argocd["config"]["base_url"] == "https://argocd.example.com"
    assert argocd["config"]["bearer_token"] == "tok_env"
    assert argocd["config"]["project"] == "payments"
    assert argocd["config"]["app_namespace"] == "argocd"
    assert argocd["config"]["verify_ssl"] is False


def test_resolve_effective_integrations_ignores_invalid_argocd_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv("ARGOCD_BASE_URL", "https://argocd.example.com")
    monkeypatch.setenv("ARGOCD_AUTH_TOKEN", "tok_env")
    monkeypatch.setenv("ARGOCD_USERNAME", "admin")
    monkeypatch.setenv("ARGOCD_PASSWORD", "pw")

    assert "argocd" not in resolve_effective_integrations()


def test_argocd_multi_instance_env_is_propagated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv(
        "ARGOCD_INSTANCES",
        """
        [
          {"name":"prod","tags":{"env":"prod"},"base_url":"https://prod.argocd.example.com","bearer_token":"prod-token"},
          {"name":"stage","tags":{"env":"stage"},"credentials":{"base_url":"https://stage.argocd.example.com","bearer_token":"stage-token"}}
        ]
        """,
    )

    effective = resolve_effective_integrations()

    assert effective["argocd"]["config"]["base_url"] == "https://prod.argocd.example.com"
    assert [i["name"] for i in effective["argocd"]["instances"]] == ["prod", "stage"]
    assert effective["argocd"]["instances"][1]["config"]["bearer_token"] == "stage-token"


def test_verify_argocd_passes_with_reachable_api(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeArgoCDClient:
        def __init__(self, config: Any) -> None:
            assert config.base_url == "https://argocd.example.com"
            assert config.bearer_token == "tok_test"

        def __enter__(self) -> _FakeArgoCDClient:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def list_applications(self, **_: Any) -> dict[str, Any]:
            return {"success": True, "applications": [{"name": "payments"}], "total": 1}

    monkeypatch.setattr("app.integrations.verify.ArgoCDClient", _FakeArgoCDClient)

    result = _verify_argocd(
        "local env",
        {"base_url": "https://argocd.example.com", "bearer_token": "tok_test"},
    )

    assert result["status"] == "passed"
    assert "1 application" in result["detail"]


def test_verify_argocd_reports_missing_auth() -> None:
    result = _verify_argocd("local env", {"base_url": "https://argocd.example.com"})

    assert result["status"] == "missing"
    assert "bearer token or username/password" in result["detail"]


def test_verify_integrations_dispatches_to_argocd(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeArgoCDClient:
        def __init__(self, config: Any) -> None:
            pass

        def __enter__(self) -> _FakeArgoCDClient:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def list_applications(self, **_: Any) -> dict[str, Any]:
            return {"success": True, "applications": [], "total": 0}

    monkeypatch.setattr("app.integrations.verify.ArgoCDClient", _FakeArgoCDClient)
    monkeypatch.setattr(
        "app.integrations.catalog.load_integrations",
        lambda: [
            {
                "id": "argocd-1",
                "service": "argocd",
                "status": "active",
                "credentials": {
                    "base_url": "https://argocd.example.com",
                    "bearer_token": "tok_test",
                },
            }
        ],
    )

    results = verify_integrations("argocd")

    assert len(results) == 1
    assert results[0]["service"] == "argocd"
    assert results[0]["status"] == "passed"
