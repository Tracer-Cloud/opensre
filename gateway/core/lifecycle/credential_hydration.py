"""Load one tenant's integration credentials, once, before the gateway serves.

A silo always reads its **bootstrap secret** from Secrets Manager first. That
secret may carry a ``credentials_api_token`` for the credentials-API route
below. It never carries integrations, and it is not a database DSN. This
process does not poll remote agent runs.

Integrations then arrive by exactly one of two routes:

* **integrations secret** — read the tenant's IntegrationStore v2 blob from
  Secrets Manager. This is the route deployed silos run on, and it wins when
  both are configured.
* **credentials API** — fetch the store from the webapp over HTTPS. The staged
  fallback, used only when no integrations secret is configured; requires a
  token in the bootstrap secret.

Configuring neither leaves the store as shipped, which is how a laptop runs.

Two failure modes are deliberately different. Nothing configured means the
feature is off, so :meth:`CredentialHydrationConfig.from_environment` returns
``None``. Something-but-not-everything configured is a broken deployment, so it
raises rather than starting a silo that would serve the wrong tenant or no
tenant at all.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol

from config.constants.organization import organization_id
from config.constants.tenancy import (
    CREDENTIALS_API_URL_ENV,
    CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV,
    INTEGRATIONS_SECRET_ARN_ENV,
    INTEGRATIONS_STORE_PATH_ENV,
)
from integrations.credentials_api import CredentialsApiClient, hydrate_integration_store
from integrations.secrets_vault import hydrate_integration_store_from_secret


class SecretsManagerClient(Protocol):
    """Narrow boto3 Secrets Manager surface used at Gateway startup."""

    def get_secret_value(self, *, SecretId: str) -> dict[str, Any]:
        """Return exactly the configured bootstrap secret."""


@dataclass(frozen=True, slots=True)
class GatewayBootstrap:
    """Decrypted bootstrap values held in memory for this process only."""

    #: Authorizes the credentials-API route. Unused on the secret route.
    credentials_api_token: str | None = None
    #: Whether a route ran and replaced the local store, as opposed to the
    #: store shipping with the image. Drives one status word.
    integrations_hydrated: bool = False


@dataclass(frozen=True, slots=True)
class CredentialHydrationConfig:
    """Non-secret references required to hydrate one tenant."""

    organization_id: str
    bootstrap_secret_arn: str
    credentials_api_url: str | None = None
    integrations_secret_arn: str | None = None

    @classmethod
    def from_environment(cls) -> CredentialHydrationConfig | None:
        """Return ``None`` when disabled, and reject partial configuration.

        The bootstrap secret ARN is what says "the control plane provisioned
        this silo" — only it creates that secret. The organization name cannot
        answer that, because every deployment serves an organization; keying off
        one would read an EC2 deployment as a half-configured silo and raise at
        startup.
        """
        bootstrap_secret_arn = os.getenv(CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV, "").strip()
        credentials_api_url = os.getenv(CREDENTIALS_API_URL_ENV, "").strip()
        integrations_secret_arn = os.getenv(INTEGRATIONS_SECRET_ARN_ENV, "").strip()

        # No silo variable set anywhere: the feature is off, not misconfigured.
        if not (bootstrap_secret_arn or credentials_api_url or integrations_secret_arn):
            return None
        # Any of them set means a silo meant to hydrate, so both halves of its
        # identity — who it serves, and where its bootstrap secret lives — must
        # be present.
        organization = organization_id()
        if not (organization and bootstrap_secret_arn):
            raise ValueError("Credential hydration configuration is incomplete")
        if credentials_api_url and not credentials_api_url.lower().startswith("https://"):
            raise ValueError("Credentials API URL must use HTTPS")
        # The secret is materialized as one file that transports (unscoped, at
        # startup) and turns (under the organization's scope) both read; only an
        # explicit path makes those two resolutions the same file.
        if integrations_secret_arn and not os.getenv(INTEGRATIONS_STORE_PATH_ENV, "").strip():
            raise ValueError("Credential hydration needs an explicit integrations store path")
        return cls(
            organization_id=organization,
            credentials_api_url=credentials_api_url or None,
            bootstrap_secret_arn=bootstrap_secret_arn,
            integrations_secret_arn=integrations_secret_arn or None,
        )


def _parse_bootstrap_secret(secret_string: str) -> GatewayBootstrap:
    """Accept a legacy raw token or a secret-safe JSON bootstrap bundle."""
    if not secret_string:
        raise ValueError("Bootstrap secret is empty")
    try:
        value = json.loads(secret_string)
    except json.JSONDecodeError:
        return GatewayBootstrap(credentials_api_token=secret_string)
    if not isinstance(value, dict):
        raise ValueError("Bootstrap secret has an invalid shape")
    token = value.get("credentials_api_token")
    if token is not None and (not isinstance(token, str) or not token):
        raise ValueError("Bootstrap secret has an invalid shape")
    return GatewayBootstrap(credentials_api_token=token)


class GatewayCredentialHydrator:
    """Fetch one allowed secret, then materialize the validated local v2 store."""

    def __init__(
        self,
        *,
        config: CredentialHydrationConfig,
        secrets_client: SecretsManagerClient,
    ) -> None:
        self._config = config
        self._secrets_client = secrets_client
        #: Version of the integrations secret the local store was last built from.
        self._integrations_version: str | None = None

    @property
    def refreshes(self) -> bool:
        """Whether this hydrator can pick up a changed secret while the gateway runs."""
        return self._config.integrations_secret_arn is not None

    def refresh_if_changed(self) -> bool:
        """Reload the store when the organization's integrations secret has a new version.

        The web app writes a credential saved on its Integrations page to that
        secret; this is how the change reaches a running gateway. Returns whether
        anything was reloaded. Only the secret route refreshes.
        """
        if self._config.integrations_secret_arn is None:
            return False
        secret_string, version = self._read_secret(
            self._config.integrations_secret_arn, secret_name="Integrations"
        )
        if version == self._integrations_version:
            return False
        self._replace_store(secret_string)
        self._integrations_version = version
        return True

    def _replace_store(self, secret_string: str) -> None:
        """Materialize the store at the silo's explicit path.

        Transports read the store at startup without a scope and turns read it
        under the organization's scope; the explicit path that hydration
        requires (see :meth:`CredentialHydrationConfig.from_environment`) is the
        one file both resolve to.
        """
        hydrate_integration_store_from_secret(secret_string)

    @classmethod
    def from_environment(cls) -> GatewayCredentialHydrator | None:
        """Compose the production hydrator from task-role AWS credentials."""
        config = CredentialHydrationConfig.from_environment()
        if config is None:
            return None
        import boto3

        return cls(config=config, secrets_client=boto3.client("secretsmanager"))

    def hydrate(self) -> GatewayBootstrap:
        """Read the bootstrap secret, then load integrations by one route."""
        bootstrap_string, _version = self._read_secret(
            self._config.bootstrap_secret_arn, secret_name="Bootstrap"
        )
        bootstrap = _parse_bootstrap_secret(bootstrap_string)
        # The tenant's secret wins when both are configured: it is the route the
        # webapp maintains through the control plane, and the one deployed silos
        # run on. The credentials API stays as the staged fallback.
        if self._config.integrations_secret_arn is not None:
            self._load_from_integrations_secret()
        elif self._config.credentials_api_url is not None:
            self._load_from_credentials_api(bootstrap)
        else:
            return bootstrap
        return replace(bootstrap, integrations_hydrated=True)

    def _read_secret(self, secret_arn: str, *, secret_name: str) -> tuple[str, str | None]:
        """Read one pinned ARN: its string and version. ``secret_name`` only names it in the error."""
        response = self._secrets_client.get_secret_value(SecretId=secret_arn)
        secret_string = response.get("SecretString")
        if not isinstance(secret_string, str):
            raise ValueError(f"{secret_name} secret has no string value")
        version = response.get("VersionId")
        return secret_string, version if isinstance(version, str) else None

    def _load_from_integrations_secret(self) -> None:
        """Replace the local store from this tenant's Secrets Manager blob."""
        if self._config.integrations_secret_arn is None:
            raise ValueError("Integrations secret ARN is not configured")
        secret_string, version = self._read_secret(
            self._config.integrations_secret_arn, secret_name="Integrations"
        )
        self._replace_store(secret_string)
        self._integrations_version = version

    def _load_from_credentials_api(self, bootstrap: GatewayBootstrap) -> None:
        """Replace the local store from the webapp over HTTPS."""
        if bootstrap.credentials_api_token is None:
            raise ValueError("Bootstrap secret has no credentials API token")
        if self._config.credentials_api_url is None:
            raise ValueError("Credentials API URL is not configured")
        with CredentialsApiClient(
            base_url=self._config.credentials_api_url,
            bootstrap_credential=bootstrap.credentials_api_token,
        ) as client:
            hydrate_integration_store(
                client=client,
                organization_id=self._config.organization_id,
            )


def watch_credential_changes(
    hydrator: GatewayCredentialHydrator,
    stop: threading.Event,
    *,
    interval_seconds: float,
    on_reload: Callable[[], None],
    on_error: Callable[[BaseException], None],
) -> None:
    """Reload the store whenever the organization's secret changes, until ``stop`` is set.

    One failed check, or a failed ``on_reload``, is reported and the next check
    still runs: a transient error must not leave the gateway on stale
    credentials for good. Nothing is reported once ``stop`` is set, so a read
    that outlives shutdown cannot republish status afterwards.
    """
    while not stop.wait(interval_seconds):
        try:
            reloaded = hydrator.refresh_if_changed()
            if reloaded and not stop.is_set():
                on_reload()
        except Exception as exc:
            if not stop.is_set():
                on_error(exc)


__all__ = [
    "CredentialHydrationConfig",
    "GatewayBootstrap",
    "GatewayCredentialHydrator",
    "SecretsManagerClient",
    "watch_credential_changes",
]
