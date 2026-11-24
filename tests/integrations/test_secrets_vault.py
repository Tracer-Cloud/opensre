"""Tests for hydrating the local store from a Secrets Manager v2 payload."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.constants.paths import integrations_store_path
from config.constants.tenancy import INTEGRATIONS_STORE_PATH_ENV
from integrations import store
from integrations.secrets_vault import (
    IntegrationsSecretError,
    hydrate_integration_store_from_secret,
    parse_integrations_secret,
)

_VALID_SECRET = json.dumps(
    {
        "version": 2,
        "integrations": [
            {
                "id": "github-1",
                "service": "github",
                "status": "active",
                "instances": [
                    {
                        "name": "default",
                        "tags": {},
                        "credentials": {"token": "vault-only"},
                    }
                ],
            }
        ],
    }
)


def test_hydrate_replaces_the_local_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "integrations.json"
    monkeypatch.setattr(store, "STORE_PATH", path)
    store.upsert_integration("stale", {"credentials": {"token": "removed"}})

    validated = hydrate_integration_store_from_secret(_VALID_SECRET)

    assert [record.service for record in validated.integrations] == ["github"]
    assert [record["service"] for record in store.load_integrations()] == ["github"]


@pytest.mark.parametrize(
    "secret_string",
    [
        "",
        "   ",
        "not-json",
        json.dumps({"version": 1, "integrations": []}),
        json.dumps({"version": 2, "integrations": [{"service": "github"}]}),
        json.dumps({"version": 2, "integrations": [], "extra": True}),
    ],
)
def test_unusable_secret_is_rejected_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    secret_string: str,
) -> None:
    monkeypatch.setattr(store, "STORE_PATH", tmp_path / "integrations.json")

    with pytest.raises(IntegrationsSecretError) as excinfo:
        parse_integrations_secret(secret_string)

    message = str(excinfo.value)
    assert message.startswith("Integrations secret is")
    if secret_string.strip():
        assert secret_string.strip() not in message


def test_store_path_env_override_targets_ephemeral_storage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    override = tmp_path / "ephemeral" / "integrations.json"
    monkeypatch.setenv(INTEGRATIONS_STORE_PATH_ENV, str(override))
    monkeypatch.setattr(store, "STORE_PATH", None)

    assert integrations_store_path() == override

    hydrate_integration_store_from_secret(_VALID_SECRET)

    assert json.loads(override.read_text())["version"] == 2


def test_a_members_personal_connection_never_enters_the_shared_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Every member's turn reads this store, so it holds only what the workspace owns."""
    monkeypatch.setattr(store, "STORE_PATH", tmp_path / "integrations.json")
    monkeypatch.setenv("ORGANIZATION_ID", "org_1")

    def _instance(name: str, tags: dict[str, str]) -> dict[str, object]:
        return {"name": name, "tags": tags, "credentials": {"token": name}}

    secret = json.dumps(
        {
            "version": 2,
            "integrations": [
                {
                    "id": "github-1",
                    "service": "github",
                    "status": "active",
                    "instances": [
                        _instance("workspace", {"owner_kind": "organization", "owner_id": "org_1"}),
                        _instance("alice", {"owner_kind": "user", "owner_id": "user_alice"}),
                        _instance("other", {"owner_kind": "organization", "owner_id": "org_2"}),
                    ],
                }
            ],
        }
    )

    hydrate_integration_store_from_secret(secret)

    (record,) = store.load_integrations()
    assert [instance["name"] for instance in record["instances"]] == ["workspace"]
