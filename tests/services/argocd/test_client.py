"""Tests for the Argo CD API client."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.services.argocd import ArgoCDClient, ArgoCDConfig, make_argocd_client


def _application_payload(name: str = "payments-api") -> dict[str, Any]:
    return {
        "metadata": {"name": name, "namespace": "argocd"},
        "spec": {
            "project": "default",
            "source": {"repoURL": "https://github.com/example/payments", "targetRevision": "main"},
            "destination": {"server": "https://kubernetes.default.svc", "namespace": "payments"},
        },
        "status": {
            "sync": {"status": "OutOfSync", "revision": "abc123"},
            "health": {"status": "Degraded", "message": "Deployment unavailable"},
            "history": [
                {"revision": "old111", "deployedAt": "2026-04-01T00:00:00Z", "id": 1},
                {"revision": "abc123", "deployedAt": "2026-04-02T00:00:00Z", "id": 2},
            ],
            "operationState": {"phase": "Succeeded", "syncResult": {"revision": "abc123"}},
            "summary": {"images": ["registry.example.com/payments:abc123"]},
        },
    }


def test_config_normalizes_base_url_bearer_prefix_and_verify_ssl() -> None:
    config = ArgoCDConfig(base_url="https://argocd.example.com/", bearer_token="Bearer tok_test")
    insecure_config = ArgoCDConfig(
        base_url="https://argocd.example.com/", bearer_token="tok_test", verify_ssl="false"
    )

    assert config.base_url == "https://argocd.example.com"
    assert config.bearer_token == "tok_test"
    assert config.verify_ssl is True
    assert insecure_config.verify_ssl is False


def test_make_argocd_client_requires_base_url_and_auth() -> None:
    assert make_argocd_client("", bearer_token="tok") is None
    assert make_argocd_client("https://argocd.example.com") is None
    assert make_argocd_client("https://argocd.example.com", bearer_token="tok") is not None
    assert (
        make_argocd_client("https://argocd.example.com", username="admin", password="pw")
        is not None
    )


def test_config_rejects_plain_http_except_loopback() -> None:
    with pytest.raises(ValidationError):
        ArgoCDConfig(base_url="http://argocd.example.com", bearer_token="tok")

    local = ArgoCDConfig(base_url="http://127.0.0.1:8080", bearer_token="tok")
    assert local.base_url == "http://127.0.0.1:8080"

    ipv6_local = ArgoCDConfig(base_url="http://[::1]:8080", bearer_token="tok")
    assert ipv6_local.base_url == "http://[::1]:8080"
    assert make_argocd_client("http://argocd.example.com", bearer_token="tok") is None


def test_config_rejects_ambiguous_auth_methods() -> None:
    with pytest.raises(ValidationError, match="one auth method"):
        ArgoCDConfig(
            base_url="https://argocd.example.com",
            bearer_token="tok_test",
            username="admin",
            password="pw",
        )

    assert (
        make_argocd_client(
            "https://argocd.example.com",
            bearer_token="tok_test",
            username="admin",
            password="pw",
        )
        is None
    )


def test_config_only_strips_bearer_prefix_from_bearer_token() -> None:
    token_config = ArgoCDConfig(
        base_url="https://argocd.example.com",
        bearer_token="Bearer tok_prefixed",
        project="bearer platform",
        app_namespace="bearer namespace",
        integration_id="bearer integration",
    )
    assert token_config.bearer_token == "tok_prefixed"
    assert token_config.project == "bearer platform"
    assert token_config.app_namespace == "bearer namespace"
    assert token_config.integration_id == "bearer integration"

    login_config = ArgoCDConfig(
        base_url="https://argocd.example.com",
        username="bearer admin",
        password="bearer password",
    )
    assert login_config.username == "bearer admin"
    assert login_config.password == "bearer password"


def test_list_applications_uses_bearer_auth_and_normalizes_status() -> None:
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        assert request.headers["authorization"] == "Bearer tok_test"
        assert request.url.path == "/api/v1/applications"
        return httpx.Response(200, json={"items": [_application_payload()]}, request=request)

    client = ArgoCDClient(
        ArgoCDConfig(base_url="https://argocd.example.com", bearer_token="tok_test"),
        transport=httpx.MockTransport(handler),
    )

    result = client.list_applications(projects=["default"], selector="app=payments")

    assert result["success"] is True
    assert result["total"] == 1
    app = result["applications"][0]
    assert app["name"] == "payments-api"
    assert app["sync_status"] == "OutOfSync"
    assert app["health_status"] == "Degraded"
    assert app["revision"] == "abc123"
    assert app["history_count"] == 2
    assert "projects=default" in str(seen_requests[0].url)
    assert "selector=app%3Dpayments" in str(seen_requests[0].url)


def test_username_password_login_fetches_session_token_lazily() -> None:
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        seen.append((request.method, request.url.path, auth))
        if request.method == "POST" and request.url.path == "/api/v1/session":
            assert json.loads(request.content.decode()) == {"username": "admin", "password": "pw"}
            return httpx.Response(200, json={"token": "session-token"}, request=request)
        assert auth == "Bearer session-token"
        return httpx.Response(200, json={"items": [_application_payload()]}, request=request)

    client = ArgoCDClient(
        ArgoCDConfig(base_url="https://argocd.example.com", username="admin", password="pw"),
        transport=httpx.MockTransport(handler),
    )

    assert client.list_applications()["success"] is True
    assert seen[0] == ("POST", "/api/v1/session", "")
    assert seen[1][0:2] == ("GET", "/api/v1/applications")
    assert seen[1][2] == "Bearer session-token"


def test_expired_session_token_is_cleared_and_retried_once() -> None:
    seen: list[tuple[str, str, str]] = []
    issued_tokens = iter(["expired-session-token", "fresh-session-token"])

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        seen.append((request.method, request.url.path, auth))
        if request.method == "POST" and request.url.path == "/api/v1/session":
            return httpx.Response(200, json={"token": next(issued_tokens)}, request=request)
        if auth == "Bearer expired-session-token":
            return httpx.Response(401, text="session expired", request=request)
        assert auth == "Bearer fresh-session-token"
        return httpx.Response(200, json=_application_payload(), request=request)

    client = ArgoCDClient(
        ArgoCDConfig(base_url="https://argocd.example.com", username="admin", password="pw"),
        transport=httpx.MockTransport(handler),
    )

    result = client.get_application_summary("payments-api")

    assert result["success"] is True
    assert result["application"]["name"] == "payments-api"
    assert seen == [
        ("POST", "/api/v1/session", ""),
        ("GET", "/api/v1/applications/payments-api", "Bearer expired-session-token"),
        ("POST", "/api/v1/session", ""),
        ("GET", "/api/v1/applications/payments-api", "Bearer fresh-session-token"),
    ]


def test_expired_session_token_final_401_redacts_retired_tokens() -> None:
    seen: list[tuple[str, str, str]] = []
    issued_tokens = iter(["expired-session-token", "second-expired-session-token"])

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        seen.append((request.method, request.url.path, auth))
        if request.method == "POST" and request.url.path == "/api/v1/session":
            return httpx.Response(200, json={"token": next(issued_tokens)}, request=request)
        return httpx.Response(
            401,
            text=f"session expired for {auth} password leaked-password",
            request=request,
        )

    client = ArgoCDClient(
        ArgoCDConfig(base_url="https://argocd.example.com", username="admin", password="pw"),
        transport=httpx.MockTransport(handler),
    )

    result = client.get_application_summary("payments-api")

    assert result["success"] is False
    assert "401" in result["error"]
    assert "expired-session-token" not in result["error"]
    assert "second-expired-session-token" not in result["error"]
    assert "leaked-password" not in result["error"]
    assert "[REDACTED]" in result["error"]
    assert [call[0] for call in seen] == ["POST", "GET", "POST", "GET"]


def test_get_application_summary_includes_revision_history_and_operation_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/applications/payments-api"
        return httpx.Response(200, json=_application_payload(), request=request)

    client = ArgoCDClient(
        ArgoCDConfig(base_url="https://argocd.example.com", bearer_token="tok_test"),
        transport=httpx.MockTransport(handler),
    )

    result = client.get_application_summary(
        "payments-api", project="default", app_namespace="argocd"
    )

    assert result["success"] is True
    assert result["application"]["name"] == "payments-api"
    assert result["application"]["project"] == "default"
    assert result["application"]["operation_phase"] == "Succeeded"
    assert [h["revision"] for h in result["recent_history"]] == ["abc123", "old111"]


def test_get_application_diff_reports_drift_from_server_side_diff() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/applications/payments-api/server-side-diff"
        return httpx.Response(
            200,
            json={
                "diffs": [
                    {
                        "group": "apps",
                        "kind": "Deployment",
                        "name": "payments-api",
                        "namespace": "payments",
                        "diff": "- replicas: 3\n+ replicas: 2",
                    }
                ]
            },
            request=request,
        )

    client = ArgoCDClient(
        ArgoCDConfig(base_url="https://argocd.example.com", bearer_token="tok_test"),
        transport=httpx.MockTransport(handler),
    )

    result = client.get_application_diff("payments-api", project="default", app_namespace="argocd")

    assert result["success"] is True
    assert result["drift_detected"] is True
    assert result["diff_count"] == 1
    assert result["diffs"][0]["kind"] == "Deployment"


def test_get_application_diff_accepts_argocd_items_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/applications/payments-api/server-side-diff"
        return httpx.Response(
            200,
            json={
                "modified": True,
                "items": [
                    {
                        "group": "apps",
                        "kind": "Deployment",
                        "name": "payments-api",
                        "namespace": "payments",
                        "modified": True,
                        "diff": "- replicas: 3\n+ replicas: 2",
                    }
                ],
            },
            request=request,
        )

    client = ArgoCDClient(
        ArgoCDConfig(base_url="https://argocd.example.com", bearer_token="tok_test"),
        transport=httpx.MockTransport(handler),
    )

    result = client.get_application_diff("payments-api")

    assert result["success"] is True
    assert result["drift_detected"] is True
    assert result["diff_count"] == 1
    assert result["diffs"][0]["kind"] == "Deployment"


def test_get_application_diff_falls_back_to_managed_resources_when_server_side_diff_empty() -> None:
    seen_paths: list[str] = []
    target_state = json.dumps(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "payments-api", "namespace": "payments"},
            "spec": {"replicas": 1},
        },
        sort_keys=True,
    )
    live_state = json.dumps(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "payments-api", "namespace": "payments"},
            "spec": {"replicas": 2},
        },
        sort_keys=True,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path.endswith("/server-side-diff"):
            return httpx.Response(200, json={"modified": False}, request=request)
        assert request.url.path.endswith("/managed-resources")
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "group": "apps",
                        "kind": "Deployment",
                        "name": "payments-api",
                        "namespace": "payments",
                        "targetState": target_state,
                        "normalizedLiveState": live_state,
                    }
                ]
            },
            request=request,
        )

    client = ArgoCDClient(
        ArgoCDConfig(base_url="https://argocd.example.com", bearer_token="tok_test"),
        transport=httpx.MockTransport(handler),
    )

    result = client.get_application_diff("payments-api")

    assert result["success"] is True
    assert result["drift_detected"] is True
    assert result["diff_count"] == 1
    assert result["diffs"][0]["kind"] == "Deployment"
    assert "replicas" in result["diffs"][0]["diff"]
    assert seen_paths == [
        "/api/v1/applications/payments-api/server-side-diff",
        "/api/v1/applications/payments-api/managed-resources",
    ]


def test_get_application_diff_fallback_ignores_equal_managed_resource_states() -> None:
    state = json.dumps(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "payments-api", "namespace": "payments"},
            "spec": {"replicas": 1},
        },
        sort_keys=True,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/server-side-diff"):
            return httpx.Response(200, json={"modified": False}, request=request)
        assert request.url.path.endswith("/managed-resources")
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "group": "apps",
                        "kind": "Deployment",
                        "name": "payments-api",
                        "namespace": "payments",
                        "targetState": state,
                        "normalizedLiveState": state,
                    }
                ]
            },
            request=request,
        )

    client = ArgoCDClient(
        ArgoCDConfig(base_url="https://argocd.example.com", bearer_token="tok_test"),
        transport=httpx.MockTransport(handler),
    )

    result = client.get_application_diff("payments-api")

    assert result["success"] is True
    assert result["drift_detected"] is False
    assert result["diff_count"] == 0
    assert result["diffs"] == []


def test_get_application_diff_fallback_skips_non_json_managed_resource_states() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/server-side-diff"):
            return httpx.Response(200, json={"modified": False}, request=request)
        assert request.url.path.endswith("/managed-resources")
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "group": "",
                        "kind": "ConfigMap",
                        "name": "payments-config",
                        "namespace": "payments",
                        "targetState": "kind: ConfigMap\nmetadata:\n  name: payments-config\n",
                        "normalizedLiveState": "kind: ConfigMap\nmetadata: {name: payments-config}\n",
                    }
                ]
            },
            request=request,
        )

    client = ArgoCDClient(
        ArgoCDConfig(base_url="https://argocd.example.com", bearer_token="tok_test"),
        transport=httpx.MockTransport(handler),
    )

    result = client.get_application_diff("payments-config")

    assert result["success"] is True
    assert result["drift_detected"] is False
    assert result["diff_count"] == 0


def test_get_application_diff_fallback_redacts_sensitive_non_secret_json_state() -> None:
    target_state = json.dumps(
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "payments-config", "namespace": "payments"},
            "data": {"clientSecret": "old-super-secret-value"},
        },
        sort_keys=True,
    )
    live_state = json.dumps(
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "payments-config", "namespace": "payments"},
            "data": {"clientSecret": "new-super-secret-value"},
        },
        sort_keys=True,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/server-side-diff"):
            return httpx.Response(200, json={"modified": False}, request=request)
        assert request.url.path.endswith("/managed-resources")
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "group": "",
                        "kind": "ConfigMap",
                        "name": "payments-config",
                        "namespace": "payments",
                        "targetState": target_state,
                        "normalizedLiveState": live_state,
                    }
                ]
            },
            request=request,
        )

    client = ArgoCDClient(
        ArgoCDConfig(base_url="https://argocd.example.com", bearer_token="tok_test"),
        transport=httpx.MockTransport(handler),
    )

    result = client.get_application_diff("payments-config")

    assert result["success"] is True
    assert result["drift_detected"] is True
    assert result["diff_count"] == 1
    rendered = result["diffs"][0]["diff"]
    assert "old-super-secret-value" not in rendered
    assert "new-super-secret-value" not in rendered
    assert rendered == "[REDACTED secret-bearing resource diff]"


def test_get_application_diff_redacts_secret_resources_and_token_like_values() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "diffs": [
                    {
                        "group": "",
                        "kind": "Secret",
                        "name": "payments-secret",
                        "namespace": "payments",
                        "diff": "+ opaque: dummy-sensitive-value",
                    },
                    {
                        "group": "",
                        "kind": "ConfigMap",
                        "name": "payments-config",
                        "namespace": "payments",
                        "diff": "+ auth: Bearer dummy-sensitive-value",
                    },
                ]
            },
            request=request,
        )

    client = ArgoCDClient(
        ArgoCDConfig(base_url="https://argocd.example.com", bearer_token="tok_test"),
        transport=httpx.MockTransport(handler),
    )

    result = client.get_application_diff("payments-api")

    assert result["success"] is True
    rendered = "\n".join(diff["diff"] for diff in result["diffs"])
    assert "dummy-sensitive-value" not in rendered
    assert "[REDACTED" in rendered


def test_http_errors_are_returned_without_leaking_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden for token tok_secret", request=request)

    client = ArgoCDClient(
        ArgoCDConfig(base_url="https://argocd.example.com", bearer_token="tok_secret"),
        transport=httpx.MockTransport(handler),
    )

    result = client.get_application_summary("payments-api")

    assert result["success"] is False
    assert "403" in result["error"]
    assert "tok_secret" not in result["error"]
    assert "[REDACTED]" in result["error"]


def test_unauthorized_errors_are_returned_without_leaking_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            text="unauthorized for bearer tok_secret password leaked-password",
            request=request,
        )

    client = ArgoCDClient(
        ArgoCDConfig(base_url="https://argocd.example.com", bearer_token="tok_secret"),
        transport=httpx.MockTransport(handler),
    )

    result = client.get_application_summary("payments-api")

    assert result["success"] is False
    assert "401" in result["error"]
    assert "tok_secret" not in result["error"]
    assert "leaked-password" not in result["error"]
    assert "[REDACTED]" in result["error"]


def test_large_diffs_are_truncated_after_redaction() -> None:
    large_diff = "+ replicas: 3\n" + ("+ safe-config: value\n" * 700)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "diffs": [
                    {
                        "group": "apps",
                        "kind": "Deployment",
                        "name": "payments-api",
                        "namespace": "payments",
                        "diff": large_diff,
                    }
                ]
            },
            request=request,
        )

    client = ArgoCDClient(
        ArgoCDConfig(base_url="https://argocd.example.com", bearer_token="tok_test"),
        transport=httpx.MockTransport(handler),
    )

    result = client.get_application_diff("payments-api")

    assert result["success"] is True
    rendered = result["diffs"][0]["diff"]
    assert len(rendered) < len(large_diff)
    assert "[truncated after 10000 chars]" in rendered


def test_list_applications_omits_empty_project_and_selector_params() -> None:
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json={"items": []}, request=request)

    client = ArgoCDClient(
        ArgoCDConfig(base_url="https://argocd.example.com", bearer_token="tok_test"),
        transport=httpx.MockTransport(handler),
    )

    result = client.list_applications(projects=["", "   "], selector="   ")

    assert result["success"] is True
    assert seen_requests[0].url.query == b""


def test_verify_ssl_false_is_passed_to_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("app.services.argocd.client.httpx.Client", FakeClient)
    client = ArgoCDClient(
        ArgoCDConfig(
            base_url="https://argocd.example.com",
            bearer_token="tok_test",
            verify_ssl=False,
        )
    )

    assert client._get_client() is not None
    assert captured["verify"] is False
