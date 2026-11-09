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
from integrations.credentials_api import IntegrationStoreV2

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
                    "instances": [
                        {"name": "default", "tags": {}, "credentials": {"auth_token": token}}
                    ],
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
        self.bootstrap = json.dumps({})

    def rotate(self, token: str, version: str) -> None:
        self.value = _store(token)
        self.version = version

    def get_secret_value(self, *, SecretId: str) -> dict[str, Any]:
        if SecretId == _BOOTSTRAP_ARN:
            return {"SecretString": self.bootstrap, "VersionId": "b1"}
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


class _RotatingApi:
    """Credentials-API stand-in whose credential set can change between fetches."""

    served: list[str] = ["token-one"]
    fetches = 0

    def __init__(self, *, bootstrap_credential: str, **_kwargs: object) -> None:
        self.bootstrap_credential = bootstrap_credential

    def __enter__(self) -> _RotatingApi:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def fetch(self, organization_id: str) -> IntegrationStoreV2:
        assert organization_id == "org-a"
        type(self).fetches += 1
        return IntegrationStoreV2.model_validate(json.loads(_store(type(self).served[0])))


def test_the_credentials_api_route_reloads_only_when_the_fetched_set_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hint promises a reload on every route; the API fallback keeps that promise."""
    # Arrange: hydrated from the API, every store write recorded instead of persisted
    written: list[IntegrationStoreV2] = []

    def fetch_and_record(*, client: Any, organization_id: str) -> IntegrationStoreV2:
        fetched = client.fetch(organization_id)
        written.append(fetched)
        return fetched

    monkeypatch.setattr(credential_hydration, "CredentialsApiClient", _RotatingApi)
    monkeypatch.setattr(credential_hydration, "hydrate_integration_store", fetch_and_record)
    monkeypatch.setattr(credential_hydration, "materialize_integration_store", written.append)
    _RotatingApi.served = ["token-one"]
    _RotatingApi.fetches = 0
    secrets = _VersionedSecrets()
    secrets.bootstrap = json.dumps({"credentials_api_token": "bootstrap-token"})
    hydrator = GatewayCredentialHydrator(
        config=CredentialHydrationConfig(
            organization_id="org-a",
            bootstrap_secret_arn=_BOOTSTRAP_ARN,
            credentials_api_url="https://credentials.example.test",
        ),
        secrets_client=secrets,
    )
    hydrator.hydrate()

    # Act
    unchanged = hydrator.refresh_if_changed()
    _RotatingApi.served = ["token-two"]
    changed = hydrator.refresh_if_changed()
    again = hydrator.refresh_if_changed()

    # Assert: the route refreshes; only the changed set is rewritten, with the new token
    assert hydrator.refreshes and (unchanged, changed, again) == (False, True, False)
    assert _RotatingApi.fetches == 4 and len(written) == 2
    assert written[1].integrations[0].instances[0].credentials["auth_token"] == "token-two"


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


def test_a_reload_that_fails_to_report_does_not_end_the_watcher(
    hydrated: tuple[GatewayCredentialHydrator, _VersionedSecrets, list[str]],
) -> None:
    # Arrange: reporting the first reload raises (a status file that cannot be written)
    hydrator, secrets, _written = hydrated
    stop = threading.Event()
    outcomes: list[str] = []
    secrets.rotate("token-two", "v2")

    def reloaded() -> None:
        if len(outcomes) == 0:
            outcomes.append("report-failed")
            secrets.rotate("token-three", "v3")
            raise OSError("cannot write status")
        outcomes.append("reported")
        stop.set()

    # Act
    watch_credential_changes(
        hydrator,
        stop,
        interval_seconds=0.01,
        on_reload=reloaded,
        on_error=lambda exc: outcomes.append(type(exc).__name__),
    )

    # Assert: the failure is reported as an error and the next reload is still delivered
    assert outcomes == ["report-failed", "OSError", "reported"]


def test_nothing_is_reported_once_stop_is_set(
    hydrated: tuple[GatewayCredentialHydrator, _VersionedSecrets, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A secret read that outlives shutdown must not republish status afterwards."""
    # Arrange: the check sets stop while it runs, as a shutdown during a slow read would
    hydrator, secrets, _written = hydrated
    stop = threading.Event()
    reported: list[str] = []
    real_refresh = hydrator.refresh_if_changed

    def slow_refresh() -> bool:
        secrets.rotate("token-two", "v2")
        stop.set()
        return real_refresh()

    monkeypatch.setattr(hydrator, "refresh_if_changed", slow_refresh)

    # Act
    watch_credential_changes(
        hydrator,
        stop,
        interval_seconds=0.01,
        on_reload=lambda: reported.append("reloaded"),
        on_error=lambda exc: reported.append(type(exc).__name__),
    )

    # Assert
    assert reported == []


def test_the_controller_reloads_credentials_and_stops_its_watcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the controller: a rotated secret updates the credentials status; stop ends the thread."""
    # Arrange
    import logging
    import time

    from gateway.core.lifecycle import controller as controller_module
    from gateway.core.lifecycle.controller import GatewayController

    written: list[str] = []
    monkeypatch.setattr(
        credential_hydration, "hydrate_integration_store_from_secret", lambda s: written.append(s)
    )
    monkeypatch.setattr(controller_module, "CREDENTIAL_REFRESH_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(controller_module, "clear_component_status", lambda: None)
    monkeypatch.setattr(controller_module, "record_stopped", lambda **_kw: None)
    secrets = _VersionedSecrets()
    hydrator = GatewayCredentialHydrator(
        config=CredentialHydrationConfig(
            organization_id="org-a",
            bootstrap_secret_arn=_BOOTSTRAP_ARN,
            integrations_secret_arn=_INTEGRATIONS_ARN,
        ),
        secrets_client=secrets,
    )
    controller = GatewayController(credential_hydrator_factory=lambda: hydrator)
    published: list[str] = []
    monkeypatch.setattr(
        controller,
        "_publish_status",
        lambda _logger: published.append(controller.components["credentials"]),
    )
    logger = logging.getLogger("test-credential-refresh")

    # Act
    controller._load_credentials(logger)
    controller._start_credential_refresh_watcher(logger)
    secrets.rotate("token-two", "v2")
    deadline = time.monotonic() + 5
    while controller.components.get("credentials") != "hydrated (reloaded)":
        assert time.monotonic() < deadline, "the watcher never reloaded"
        time.sleep(0.01)
    thread = controller._credential_refresh_thread
    controller.stop(timeout=5)

    # Assert: the store was rebuilt with the new token, status was republished, the thread ended
    assert any("token-two" in s for s in written)
    assert published == ["hydrated (reloaded)"]
    assert thread is not None and not thread.is_alive()
    assert controller._credential_refresh_thread is None
