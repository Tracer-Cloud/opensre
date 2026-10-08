"""Tests for Neon credential API validation and local materialization."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from integrations.credentials_api import (
    CredentialsApiClient,
    CredentialsApiError,
    hydrate_integration_store,
    validate_integration_store_v2,
)


def _valid_payload(api_key: str = "sandbox-secret") -> dict[str, object]:
    return {
        "version": 2,
        "integrations": [
            {
                "id": "grafana-1",
                "service": "grafana",
                "status": "active",
                "instances": [
                    {
                        "name": "sandbox",
                        "tags": {"env": "test"},
                        "credentials": {
                            "endpoint": "https://stub.invalid",
                            "api_key": api_key,
                        },
                    }
                ],
            }
        ],
    }


def _client_with_transport(
    handler: httpx.MockTransport,
    *,
    token: str = "bootstrap-secret",
) -> CredentialsApiClient:
    http_client = httpx.Client(
        base_url="https://credentials.example",
        transport=handler,
        headers={"Authorization": f"Bearer {token}"},
    )
    return CredentialsApiClient(
        base_url="https://credentials.example",
        bootstrap_credential=token,
        http_client=http_client,
    )


def test_client_sends_bootstrap_auth_and_escapes_organization_id() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer bootstrap-secret"
        assert request.url.raw_path == b"/api/agent/integrations?organizationId=org%2Ftenant"
        return httpx.Response(200, json=_valid_payload())

    client = _client_with_transport(httpx.MockTransport(_handler))

    payload = client.fetch("org/tenant")

    assert payload.version == 2
    assert payload.integrations[0].service == "grafana"


def test_existing_webapp_vault_response_is_adapted_to_store_v2() -> None:
    payload = {
        "success": True,
        "data": [
            {
                "id": "integration-1",
                "service": "openai",
                "status": "active",
                "name": "default",
                "credentials": {"api_key": "test-only"},
            }
        ],
    }

    validated = validate_integration_store_v2(payload)

    assert validated.as_store_data() == {
        "version": 2,
        "integrations": [
            {
                "id": "integration-1",
                "service": "openai",
                "status": "active",
                "instances": [
                    {
                        "name": "default",
                        "tags": {},
                        "credentials": {"api_key": "test-only"},
                    }
                ],
            }
        ],
    }


def test_webapp_connection_ownership_is_preserved_as_non_secret_metadata() -> None:
    payload = {
        "success": True,
        "data": [
            {
                "id": "github-personal",
                "service": "github",
                "status": "active",
                "name": "My GitHub",
                "owner": {"kind": "user", "id": "user_123"},
                "is_default": True,
                "credentials": {"auth_token": "test-only"},
            }
        ],
    }

    validated = validate_integration_store_v2(payload)

    instance = validated.as_store_data()["integrations"][0]["instances"][0]
    assert instance["name"] == "My GitHub"
    assert instance["tags"] == {
        "owner_kind": "user",
        "owner_id": "user_123",
        "is_default": "true",
    }


def test_silo_fetch_never_hydrates_a_members_personal_connection() -> None:
    """The silo store is shared by the whole org; personal grants stay out of it."""

    def _vault_record(record_id: str, owner: dict[str, str] | None) -> dict[str, object]:
        record: dict[str, object] = {
            "id": record_id,
            "service": "github",
            "status": "active",
            "name": record_id,
            "credentials": {"auth_token": f"tok-{record_id}"},
        }
        if owner is not None:
            record["owner"] = owner
        return record

    payload = {
        "success": True,
        "data": [
            _vault_record("team", {"kind": "organization", "id": "org_1"}),
            _vault_record("legacy", None),
            _vault_record("mine", {"kind": "user", "id": "user_1"}),
            _vault_record("other-org", {"kind": "organization", "id": "org_2"}),
        ],
    }
    client = _client_with_transport(
        httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    )

    fetched = client.fetch("org_1")

    assert [record.id for record in fetched.integrations] == ["team", "legacy"]


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "integrations": []},
        {"version": 2, "integrations": [], "unexpected": True},
        {
            "version": 2,
            "integrations": [
                {
                    "id": "grafana-1",
                    "service": "grafana",
                    "status": "active",
                    "instances": [{"name": "default", "tags": {}, "credentials": []}],
                }
            ],
        },
    ],
)
def test_strict_validation_rejects_non_v2_shapes(payload: object) -> None:
    with pytest.raises(CredentialsApiError, match="invalid credential set"):
        validate_integration_store_v2(payload)


def test_validation_error_does_not_include_credential_values() -> None:
    secret = "must-not-appear"
    payload = _valid_payload(secret)
    integration = payload["integrations"]
    assert isinstance(integration, list)
    record = integration[0]
    assert isinstance(record, dict)
    record["unexpected"] = secret

    with pytest.raises(CredentialsApiError) as exc_info:
        validate_integration_store_v2(payload)

    assert secret not in str(exc_info.value)


def test_hydration_atomically_materializes_private_v2_store(tmp_path: Path) -> None:
    store_file = tmp_path / "home" / ".opensre" / "integrations.json"
    client = _client_with_transport(
        httpx.MockTransport(lambda _request: httpx.Response(200, json=_valid_payload()))
    )

    with patch("integrations.store.STORE_PATH", store_file):
        hydrate_integration_store(client=client, organization_id="org_123")

    saved = json.loads(store_file.read_text(encoding="utf-8"))
    assert saved == _valid_payload()
    if os.name != "nt":
        assert stat.S_IMODE(store_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(store_file.parent.stat().st_mode) == 0o700


def test_http_failure_is_generic_and_does_not_echo_response_secret() -> None:
    secret = "response-secret"
    client = _client_with_transport(
        httpx.MockTransport(lambda _request: httpx.Response(500, text=f"backend failed: {secret}"))
    )

    with pytest.raises(CredentialsApiError) as exc_info:
        client.fetch("org_123")

    assert str(exc_info.value) == "Unable to retrieve organization credentials"
    assert secret not in str(exc_info.value)


def test_plain_http_credentials_api_url_is_rejected() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        CredentialsApiClient(
            base_url="http://credentials.example",
            bootstrap_credential="secret",
        )
