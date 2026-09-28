"""Tests for fail-closed startup credential hydration."""

from __future__ import annotations

import json
import logging
import stat
from pathlib import Path
from typing import Any, cast

import pytest

from config.constants.billing import ORGANIZATION_ID_ENV, WEBAPP_URL_ENV
from config.constants.paths import integrations_store_path
from config.constants.tenancy import (
    CREDENTIALS_API_URL_ENV,
    CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV,
    INTEGRATIONS_SECRET_ARN_ENV,
    INTEGRATIONS_STORE_PATH_ENV,
)
from gateway.core.lifecycle.controller import GatewayController
from gateway.core.lifecycle.credential_hydration import (
    CredentialHydrationConfig,
    GatewayCredentialHydrator,
)
from gateway.core.lifecycle.errors import GatewayConfigurationError
from integrations import store
from integrations.credentials_api import IntegrationStoreV2


class _Secrets:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.calls: list[str] = []

    def get_secret_value(self, *, SecretId: str) -> dict[str, Any]:
        self.calls.append(SecretId)
        return {"SecretString": self.secret}


class _ApiClient:
    authorization: str = ""

    def __init__(self, *, bootstrap_credential: str, **_kwargs: object) -> None:
        type(self).authorization = bootstrap_credential

    def __enter__(self) -> _ApiClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def fetch(self, organization_id: str) -> IntegrationStoreV2:
        assert organization_id == "org-a"
        return IntegrationStoreV2.model_validate(
            {
                "version": 2,
                "integrations": [
                    {
                        "id": "stub-1",
                        "service": "stub",
                        "status": "active",
                        "instances": [
                            {
                                "name": "sandbox",
                                "tags": {},
                                "credentials": {"token": "runtime-only"},
                            }
                        ],
                    }
                ],
            }
        )


def test_hydrates_exact_secret_and_atomically_writes_private_v2_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "home" / ".opensre" / "integrations.json"
    monkeypatch.setattr(store, "STORE_PATH", path)
    monkeypatch.setattr(
        "gateway.core.lifecycle.credential_hydration.CredentialsApiClient",
        _ApiClient,
    )
    secrets = _Secrets(json.dumps({"credentials_api_token": "bootstrap-token"}))
    hydrator = GatewayCredentialHydrator(
        config=CredentialHydrationConfig(
            organization_id="org-a",
            credentials_api_url="https://credentials.example.test",
            bootstrap_secret_arn="arn:aws:secretsmanager:region:account:secret:org-a",
        ),
        secrets_client=secrets,
    )

    hydrator.hydrate()

    assert secrets.calls == ["arn:aws:secretsmanager:region:account:secret:org-a"]
    assert _ApiClient.authorization == "bootstrap-token"
    assert json.loads(path.read_text())["version"] == 2
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


class _SecretsById:
    """Secrets Manager fake that serves a distinct payload per pinned ARN."""

    def __init__(self, secrets: dict[str, str]) -> None:
        self.secrets = secrets
        self.calls: list[str] = []

    def get_secret_value(self, *, SecretId: str) -> dict[str, Any]:
        self.calls.append(SecretId)
        return {"SecretString": self.secrets[SecretId]}


def test_hydrates_from_integrations_secret_when_no_credentials_api_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "home" / ".opensre" / "integrations.json"
    monkeypatch.setattr(store, "STORE_PATH", path)
    bootstrap_arn = "arn:aws:secretsmanager:region:account:secret:org-a-bootstrap"
    integrations_arn = "arn:aws:secretsmanager:region:account:secret:org-a-integrations"
    secrets = _SecretsById(
        {
            bootstrap_arn: json.dumps({}),
            integrations_arn: json.dumps(
                {
                    "version": 2,
                    "integrations": [
                        {
                            "id": "grafana-1",
                            "service": "grafana",
                            "status": "active",
                            "instances": [
                                {
                                    "name": "prod",
                                    "tags": {"env": "prod"},
                                    "credentials": {"api_key": "vault-only"},
                                }
                            ],
                        }
                    ],
                }
            ),
        }
    )
    hydrator = GatewayCredentialHydrator(
        config=CredentialHydrationConfig(
            organization_id="org-a",
            bootstrap_secret_arn=bootstrap_arn,
            integrations_secret_arn=integrations_arn,
        ),
        secrets_client=secrets,
    )

    bootstrap = hydrator.hydrate()

    assert secrets.calls == [bootstrap_arn, integrations_arn]
    assert bootstrap.integrations_hydrated is True
    stored = json.loads(path.read_text())
    assert stored["version"] == 2
    assert [record["service"] for record in stored["integrations"]] == ["grafana"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_bootstrap_only_silo_stays_preseeded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "home" / ".opensre" / "integrations.json"
    monkeypatch.setattr(store, "STORE_PATH", path)
    bootstrap_arn = "arn:aws:secretsmanager:region:account:secret:org-a-bootstrap"
    secrets = _SecretsById({bootstrap_arn: json.dumps({"credentials_api_token": "token"})})
    hydrator = GatewayCredentialHydrator(
        config=CredentialHydrationConfig(
            organization_id="org-a",
            bootstrap_secret_arn=bootstrap_arn,
        ),
        secrets_client=secrets,
    )

    bootstrap = hydrator.hydrate()

    assert bootstrap.integrations_hydrated is False
    assert not path.exists()


def test_hydrated_secret_is_visible_to_integration_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The deployed contract end to end: secret → store path → resolution.

    Hydration writing a file is not the point; a turn resolving the credential
    is. This pins the whole chain under the env the control plane actually
    injects, so a change to either half fails here rather than in a silo.
    """
    store_path = tmp_path / "ephemeral" / "integrations.json"
    monkeypatch.setenv(INTEGRATIONS_STORE_PATH_ENV, str(store_path))
    monkeypatch.setattr(store, "STORE_PATH", None)
    # A silo has no webapp vault configured; local sources must serve the turn.
    monkeypatch.delenv(WEBAPP_URL_ENV, raising=False)
    monkeypatch.delenv("JWT_TOKEN", raising=False)

    bootstrap_arn = "arn:aws:secretsmanager:region:account:secret:org-a-bootstrap"
    integrations_arn = "arn:aws:secretsmanager:region:account:secret:org-a-integrations"
    secrets = _SecretsById(
        {
            bootstrap_arn: json.dumps({}),
            integrations_arn: json.dumps(
                {
                    "version": 2,
                    "integrations": [
                        {
                            "id": "grafana-1",
                            "service": "grafana",
                            "status": "active",
                            "instances": [
                                {
                                    "name": "default",
                                    "tags": {},
                                    "credentials": {
                                        "url": "https://grafana.invalid",
                                        "api_key": "vault-only",
                                    },
                                }
                            ],
                        }
                    ],
                }
            ),
        }
    )
    hydrator = GatewayCredentialHydrator(
        config=CredentialHydrationConfig(
            organization_id="org-a",
            bootstrap_secret_arn=bootstrap_arn,
            integrations_secret_arn=integrations_arn,
        ),
        secrets_client=secrets,
    )

    hydrator.hydrate()
    resolved = {entry["service"] for entry in store.load_integrations()}

    assert integrations_store_path() == store_path
    assert "grafana" in resolved


def test_environment_configures_the_integrations_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org-a")
    monkeypatch.setenv(CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV, "arn:bootstrap")
    monkeypatch.setenv(INTEGRATIONS_SECRET_ARN_ENV, "arn:integrations")
    monkeypatch.setenv(INTEGRATIONS_STORE_PATH_ENV, "/tmp/opensre/integrations.json")
    monkeypatch.delenv(CREDENTIALS_API_URL_ENV, raising=False)

    config = CredentialHydrationConfig.from_environment()

    assert config is not None
    assert config.integrations_secret_arn == "arn:integrations"
    assert config.credentials_api_url is None


def test_the_secret_route_needs_an_explicit_store_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transports read the store unscoped and turns read it under the organization's
    scope; without an explicit path those are two files, so the silo must not start."""
    # Arrange
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org-a")
    monkeypatch.setenv(CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV, "arn:bootstrap")
    monkeypatch.setenv(INTEGRATIONS_SECRET_ARN_ENV, "arn:integrations")
    monkeypatch.delenv(CREDENTIALS_API_URL_ENV, raising=False)
    monkeypatch.delenv(INTEGRATIONS_STORE_PATH_ENV, raising=False)

    # Act / Assert
    with pytest.raises(ValueError, match="explicit integrations store path"):
        CredentialHydrationConfig.from_environment()


def test_partial_environment_configuration_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A silo signal without the bootstrap secret is a broken deployment.

    The integrations ARN says the control plane provisioned this task, so a
    missing bootstrap secret is a misconfiguration rather than the feature
    being off.
    """
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org-a")
    monkeypatch.setenv(INTEGRATIONS_SECRET_ARN_ENV, "arn:aws:integrations")
    monkeypatch.delenv(CREDENTIALS_API_URL_ENV, raising=False)
    monkeypatch.delenv(CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV, raising=False)

    with pytest.raises(ValueError, match="incomplete"):
        CredentialHydrationConfig.from_environment()


def test_manager_fails_closed_with_generic_error() -> None:
    class BrokenHydrator:
        def hydrate(self) -> None:
            raise RuntimeError("secret value must not escape")

    def _broken_hydrator() -> GatewayCredentialHydrator:
        # The fake only needs hydrate(); cast keeps the factory signature honest.
        return cast(GatewayCredentialHydrator, BrokenHydrator())

    manager = GatewayController(credential_hydrator_factory=_broken_hydrator)

    with pytest.raises(GatewayConfigurationError, match="hydration failed"):
        manager._load_credentials(logging.getLogger("test"))

    assert manager.components["credentials"] == "failed"


class _SecretsByArn:
    """Serves a different payload per ARN so the chosen route is observable."""

    def __init__(self, payloads: dict[str, str]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    def get_secret_value(self, *, SecretId: str) -> dict[str, Any]:
        self.calls.append(SecretId)
        return {"SecretString": self.payloads[SecretId]}


def test_integrations_secret_wins_when_both_routes_are_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The deployed route is the tenant's secret, not the webapp API.

    Nothing failed when the module docstring described the opposite order, so
    this pins the precedence the silo actually executes.
    """
    # Arrange: both routes configured, each yielding a distinguishable service.
    monkeypatch.setattr(store, "STORE_PATH", tmp_path / "integrations.json")
    monkeypatch.setattr(
        "gateway.core.lifecycle.credential_hydration.CredentialsApiClient",
        _ApiClient,
    )
    bootstrap_arn = "arn:aws:secretsmanager:region:account:secret:bootstrap"
    integrations_arn = "arn:aws:secretsmanager:region:account:secret:integrations"
    secrets = _SecretsByArn(
        {
            bootstrap_arn: json.dumps({"credentials_api_token": "bootstrap-token"}),
            integrations_arn: json.dumps(
                {
                    "version": 2,
                    "integrations": [
                        {
                            "id": "from-secret-1",
                            "service": "from-secret",
                            "status": "active",
                            "instances": [
                                {"name": "default", "tags": {}, "credentials": {"token": "s"}}
                            ],
                        }
                    ],
                }
            ),
        }
    )
    hydrator = GatewayCredentialHydrator(
        config=CredentialHydrationConfig(
            organization_id="org-a",
            credentials_api_url="https://credentials.example.test",
            bootstrap_secret_arn=bootstrap_arn,
            integrations_secret_arn=integrations_arn,
        ),
        secrets_client=secrets,
    )

    # Act
    bootstrap = hydrator.hydrate()

    # Assert: the secret's store landed, and the API was never consulted.
    assert [record["service"] for record in store.load_integrations()] == ["from-secret"]
    assert integrations_arn in secrets.calls
    assert bootstrap.integrations_hydrated is True
