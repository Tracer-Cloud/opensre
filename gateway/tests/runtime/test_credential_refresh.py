"""A credential saved in the web app reaches a running gateway: the store reloads on a new secret version."""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from gateway.core.lifecycle import credential_hydration
from gateway.core.lifecycle.credential_hydration import (
    CredentialHydrationConfig,
    GatewayCredentialHydrator,
    watch_credential_changes,
)

_BOOTSTRAP_ARN = "arn:aws:secretsmanager:us-east-1:1:secret:bootstrap"
_INTEGRATIONS_ARN = "arn:aws:secretsmanager:us-east-1:1:secret:integrations"


def _store(token: str) -> str:
    return json.dumps(
        {
            "version": 2,
            "integrations": [
                {
                    "id": "github-1",
                    "service": "github",
                    "status": "active",
                    "instances": [{"config": {}, "credentials": {"auth_token": token}}],
                }
            ],
        }
    )


class _VersionedSecrets:
    """Secrets Manager stand-in whose integrations secret can be rotated between calls."""

    def __init__(self) -> None:
        self.version = "v1"
        self.value = _store("token-one")
        self.reads = 0

    def rotate(self, token: str, version: str) -> None:
        self.value = _store(token)
        self.version = version

    def get_secret_value(self, *, SecretId: str) -> dict[str, Any]:
        if SecretId == _BOOTSTRAP_ARN:
            return {"SecretString": json.dumps({}), "VersionId": "b1"}
        self.reads += 1
        return {"SecretString": self.value, "VersionId": self.version}


@pytest.fixture
def hydrated(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[GatewayCredentialHydrator, _VersionedSecrets, list[str]]:
    """A hydrator that has loaded version v1; every store write is recorded, not persisted."""
    written: list[str] = []

    def record(secret_string: str) -> object:
        written.append(secret_string)
        return object()

    monkeypatch.setattr(credential_hydration, "hydrate_integration_store_from_secret", record)
    secrets = _VersionedSecrets()
    hydrator = GatewayCredentialHydrator(
        config=CredentialHydrationConfig(
            organization_id="org-a",
            bootstrap_secret_arn=_BOOTSTRAP_ARN,
            integrations_secret_arn=_INTEGRATIONS_ARN,
        ),
        secrets_client=secrets,
    )
    hydrator.hydrate()
    return hydrator, secrets, written


def test_the_store_reloads_only_when_the_secret_has_a_new_version(
    hydrated: tuple[GatewayCredentialHydrator, _VersionedSecrets, list[str]],
) -> None:
    # Arrange
    hydrator, secrets, written = hydrated
    assert len(written) == 1 and hydrator.refreshes

    # Act
    unchanged = hydrator.refresh_if_changed()
    secrets.rotate("token-two", "v2")
    changed = hydrator.refresh_if_changed()
    again = hydrator.refresh_if_changed()

    # Assert: one reload, carrying the rotated token; the same version is never reloaded
    assert (unchanged, changed, again) == (False, True, False)
    assert len(written) == 2 and "token-two" in written[1]


def test_the_credentials_api_route_does_not_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setattr(
        credential_hydration, "hydrate_integration_store_from_secret", lambda _s: None
    )
    hydrator = GatewayCredentialHydrator(
        config=CredentialHydrationConfig(
            organization_id="org-a",
            bootstrap_secret_arn=_BOOTSTRAP_ARN,
            credentials_api_url="https://app.example/api",
        ),
        secrets_client=_VersionedSecrets(),
    )

    # Act / Assert
    assert hydrator.refreshes is False
    assert hydrator.refresh_if_changed() is False


def test_the_watcher_reports_a_reload_and_survives_a_failed_check(
    hydrated: tuple[GatewayCredentialHydrator, _VersionedSecrets, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the first check fails, the second sees a new version, then the watcher is stopped
    hydrator, secrets, _written = hydrated
    stop = threading.Event()
    outcomes: list[str] = []
    real_refresh = hydrator.refresh_if_changed
    calls = {"n": 0}

    def flaky_refresh() -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("secrets manager unreachable")
        secrets.rotate("token-three", "v3")
        return real_refresh()

    monkeypatch.setattr(hydrator, "refresh_if_changed", flaky_refresh)

    def reloaded() -> None:
        outcomes.append("reloaded")
        stop.set()

    # Act
    watch_credential_changes(
        hydrator,
        stop,
        interval_seconds=0.01,
        on_reload=reloaded,
        on_error=lambda exc: outcomes.append(type(exc).__name__),
    )

    # Assert: the error is reported, the next check still reloads, and the loop ends on stop
    assert outcomes == ["TimeoutError", "reloaded"]
