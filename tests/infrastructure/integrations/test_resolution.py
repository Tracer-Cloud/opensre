"""Tests for shared integration resolution."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from infrastructure.harness_providers import integration_resolution as harness_providers
from infrastructure.harness_providers import reset_harness_providers
from surfaces.shared.terminal.output.boundary import install_harness_providers


def _install_adapters(monkeypatch: Any, **fields: Any) -> None:
    """Override individual resolution adapters on the installed bundle (auto-reverts)."""
    monkeypatch.setattr(
        harness_providers,
        "_installed_adapters",
        replace(harness_providers._adapters(), **fields),
    )


@pytest.fixture(autouse=True)
def _harness_providers() -> Iterator[None]:
    install_harness_providers()
    yield
    reset_harness_providers()


def _jwt(payload: dict[str, Any]) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    return f"{header.decode()}.{body.decode()}.sig"


def test_resolve_integrations_returns_existing_state_without_lookup(monkeypatch: Any) -> None:
    def _unexpected_lookup() -> list[dict[str, Any]]:
        raise AssertionError("local store should not be queried")

    monkeypatch.setattr("integrations.store.load_integrations", _unexpected_lookup)

    resolved = harness_providers.resolve_integrations(
        {"resolved_integrations": {"datadog": {"site": "datadoghq.com"}}}
    )

    assert resolved == {"datadog": {"site": "datadoghq.com"}}


def test_resolution_request_ignores_unrelated_runtime_state() -> None:
    request = harness_providers.IntegrationResolutionRequest.model_validate(
        {
            "_auth_token": " Bearer token ",
            "org_id": " org-123 ",
            "alert_name": "checkout failed",
            "agent_messages": [{"role": "user", "content": "hello"}],
        }
    )

    assert request.auth_token == "Bearer token"
    assert request.org_id == "org-123"
    assert not hasattr(request, "alert_name")


def test_resolution_result_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        harness_providers.IntegrationResolutionResult.model_validate(
            {
                "resolved_integrations": {},
                "unexpected": True,
            }
        )


def test_resolve_local_store_sources_returns_progress_metadata(monkeypatch: Any) -> None:
    store_records = [{"service": "datadog", "status": "active", "credentials": {}}]
    monkeypatch.delenv("JWT_TOKEN", raising=False)
    _install_adapters(
        monkeypatch,
        load_integrations=lambda: store_records,
        load_env_integrations=lambda: [],
        merge_local_integrations=lambda store, env: [*store, *env],
        classify_integrations=lambda _records: {"datadog": {"site": "datadoghq.com"}},
    )

    result = harness_providers.resolve_integrations_with_metadata()

    assert result.resolved_integrations == {"datadog": {"site": "datadoghq.com"}}
    assert result.services == ("datadog",)
    assert result.progress_message == "Resolved local integrations from store: ['datadog']"


def test_resolution_result_is_strict_pydantic_model() -> None:
    result = harness_providers.IntegrationResolutionResult(
        resolved_integrations={
            "datadog": {"site": "datadoghq.com"},
            "_gateway_chat_id": "chat-1",
            "_all": [],
        },
        progress_message="Resolved",
    )

    assert result.model_dump() == {
        "resolved_integrations": {
            "datadog": {"site": "datadoghq.com"},
            "_gateway_chat_id": "chat-1",
            "_all": [],
        },
        "progress_message": "Resolved",
    }
    assert result.services == ("datadog",)


def test_resolve_env_token_merges_remote_store_and_env(monkeypatch: Any) -> None:
    remote_records = [{"service": "sentry", "status": "active", "credentials": {}}]
    store_records = [{"service": "datadog", "status": "active", "credentials": {}}]
    env_records = [{"service": "grafana", "status": "active", "credentials": {}}]
    captured_fetch: dict[str, str] = {}
    captured_merge: dict[str, list[dict[str, Any]]] = {}

    def _fetch_remote_integrations(*, org_id: str, auth_token: str) -> list[dict[str, Any]]:
        captured_fetch["org_id"] = org_id
        captured_fetch["auth_token"] = auth_token
        return remote_records

    def _merge(
        env_integrations: list[dict[str, Any]],
        store_integrations: list[dict[str, Any]],
        remote_integrations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        captured_merge["env"] = env_integrations
        captured_merge["store"] = store_integrations
        captured_merge["remote"] = remote_integrations
        return [*env_integrations, *store_integrations, *remote_integrations]

    monkeypatch.setenv("JWT_TOKEN", f"Bearer {_jwt({'organization': 'org-123'})}")
    monkeypatch.setattr(
        harness_providers,
        "fetch_remote_integrations",
        _fetch_remote_integrations,
    )
    _install_adapters(
        monkeypatch,
        load_integrations=lambda: store_records,
        load_env_integrations=lambda: env_records,
        merge_integrations_by_service=_merge,
        classify_integrations=lambda _records: {"datadog": {}, "grafana": {}, "sentry": {}},
    )

    result = harness_providers.resolve_integrations_with_metadata()

    assert captured_fetch == {
        "org_id": "org-123",
        "auth_token": _jwt({"organization": "org-123"}),
    }
    assert captured_merge == {
        "env": env_records,
        "store": store_records,
        "remote": remote_records,
    }
    assert result.resolved_integrations == {"datadog": {}, "grafana": {}, "sentry": {}}
    assert result.progress_message == (
        "Resolved integrations from remote, store, env: ['datadog', 'grafana', 'sentry']"
    )


def test_a_signed_in_laptop_resolves_the_account_integrations_over_the_store(
    monkeypatch: Any,
) -> None:
    """No JWT and no fleet vault: the account's org integrations fill the remote role."""
    # Arrange: github connected in the OpenSRE app and in the local store
    monkeypatch.delenv("JWT_TOKEN", raising=False)
    account_records = [
        {"service": "github", "status": "active", "credentials": {"auth_token": "remote"}}
    ]
    store_records = [
        {"service": "github", "status": "active", "credentials": {"auth_token": "local"}}
    ]
    captured_merge: dict[str, list[dict[str, Any]]] = {}

    def _merge(
        env_integrations: list[dict[str, Any]],
        store_integrations: list[dict[str, Any]],
        remote_integrations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        captured_merge["env"] = env_integrations
        captured_merge["store"] = store_integrations
        captured_merge["remote"] = remote_integrations
        return [*env_integrations, *store_integrations, *remote_integrations]

    _install_adapters(
        monkeypatch,
        load_integrations=lambda: store_records,
        load_env_integrations=lambda: [],
        fetch_account_integrations=lambda: account_records,
        merge_integrations_by_service=_merge,
        classify_integrations=lambda _records: {"github": {"auth_token": "remote"}},
    )

    result = harness_providers.resolve_integrations_with_metadata()

    # Assert: account records went through the remote slot of the by-service merge
    assert captured_merge == {"env": [], "store": store_records, "remote": account_records}
    assert result.resolved_integrations == {"github": {"auth_token": "remote"}}
    assert result.progress_message == "Resolved integrations from remote, store: ['github']"


def test_the_sources_stamp_tracks_the_account_generation(monkeypatch: Any) -> None:
    """A changed remote set must change the stamp, so sessions re-resolve next turn."""
    generation = {"value": 0}

    def _generation() -> int:
        return generation["value"]

    monkeypatch.setattr(harness_providers, "integrations_store_stamp", lambda: 7)
    _install_adapters(monkeypatch, account_integrations_generation=_generation)

    before = harness_providers.integration_sources_stamp()
    generation["value"] = 1
    after = harness_providers.integration_sources_stamp()

    assert before == (7, 0)
    assert after == (7, 1)


def test_resolve_without_sources_reports_empty_local_lookup(monkeypatch: Any) -> None:
    monkeypatch.delenv("JWT_TOKEN", raising=False)
    _install_adapters(monkeypatch, load_integrations=list, load_env_integrations=list)

    result = harness_providers.resolve_integrations_with_metadata()

    assert result.resolved_integrations == {}
    assert result.progress_message is not None
    assert result.progress_message.startswith("No auth context and no local integrations found")
