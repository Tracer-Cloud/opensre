"""Integration resolution, the remote fetcher, and the surface setup command.

Core resolves which integrations are active (from a remote org vault, the local
store, or env) without importing the integration store/catalog packages, and
renders an upgrade CTA without knowing a surface's slash syntax. Three concerns,
wired at three sites, each an immutable holder installed once at boot and read
through the module functions — there is no setter:

- :class:`IntegrationResolutionAdapters` — the load/merge/classify adapters
  (integrations own them; installed in ``integrations/harness_adapters``).
- :class:`RemoteIntegrationsProvider` — how org integrations are fetched from a
  remote (installed at the surface; a deployment choice, so gateway/web use the
  empty default).
- :class:`IntegrationSetupCommand` — how a surface spells "connect this
  integration" (installed at the composition root).
"""

from __future__ import annotations

import base64
import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from config.constants.paths import integrations_store_stamp
from config.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

RemoteIntegrationsFetcher = Callable[[str, str], list[dict[str, Any]]]
LoadIntegrationsFn = Callable[[], list[dict[str, Any]]]
IntegrationStorePathFn = Callable[[], str]
LoadEnvIntegrationsFn = Callable[[], list[dict[str, Any]]]
WebappVaultFetcherFn = Callable[[], list[dict[str, Any]] | None]
ClassifyIntegrationsFn = Callable[[list[dict[str, Any]]], dict[str, Any]]
MergeLocalIntegrationsFn = Callable[
    [list[dict[str, Any]], list[dict[str, Any]]], list[dict[str, Any]]
]
MergeIntegrationsByServiceFn = Callable[
    [list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]],
    list[dict[str, Any]],
]
ConfiguredIntegrationServicesFn = Callable[[], tuple[str, ...]]
SetupableIntegrationServicesFn = Callable[[], tuple[str, ...]]
AccountIntegrationsFetcherFn = Callable[[], list[dict[str, Any]]]
AccountIntegrationsGenerationFn = Callable[[], int]
FleetVaultConfiguredFn = Callable[[], bool]


def _default_fetch_remote(org_id: str, auth_token: str) -> list[dict[str, Any]]:
    _ = (org_id, auth_token)
    return []


def _default_load_integrations() -> list[dict[str, Any]]:
    return []


def _default_store_path() -> str:
    return ""


def _default_load_env_integrations() -> list[dict[str, Any]]:
    return []


def _default_classify_integrations(_records: list[dict[str, Any]]) -> dict[str, Any]:
    return {}


def _default_merge_local(
    store: list[dict[str, Any]], env: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return [*store, *env]


def _default_merge_by_service(
    env: list[dict[str, Any]],
    store: list[dict[str, Any]],
    remote: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [*env, *store, *remote]


def _default_configured_services() -> tuple[str, ...]:
    return ()


def _default_setupable_services() -> tuple[str, ...]:
    return ()


def _default_fetch_webapp_vault() -> list[dict[str, Any]] | None:
    return None


def _default_fetch_account_integrations() -> list[dict[str, Any]]:
    return []


def _default_fleet_vault_configured() -> bool:
    return False


def _default_account_integrations_generation() -> int:
    return 0


def _default_integration_setup_command(service_id: str) -> str:
    return f"integrations setup {service_id}"


def _default_select_connection(
    resolved: dict[str, Any], _connection_id: str | None
) -> dict[str, Any]:
    return resolved


@dataclass(frozen=True)
class IntegrationResolutionAdapters:
    """The load/merge/classify adapters, installed once by ``integrations``."""

    select_github_connection: Callable[[dict[str, Any], str | None], dict[str, Any]] = (
        _default_select_connection
    )
    load_integrations: LoadIntegrationsFn = _default_load_integrations
    integration_store_path: IntegrationStorePathFn = _default_store_path
    load_env_integrations: LoadEnvIntegrationsFn = _default_load_env_integrations
    classify_integrations: ClassifyIntegrationsFn = _default_classify_integrations
    merge_local_integrations: MergeLocalIntegrationsFn = _default_merge_local
    merge_integrations_by_service: MergeIntegrationsByServiceFn = _default_merge_by_service
    configured_services: ConfiguredIntegrationServicesFn = _default_configured_services
    setupable_services: SetupableIntegrationServicesFn = _default_setupable_services
    fetch_webapp_vault: WebappVaultFetcherFn = _default_fetch_webapp_vault
    fleet_vault_configured: FleetVaultConfiguredFn = _default_fleet_vault_configured
    fetch_account_integrations: AccountIntegrationsFetcherFn = _default_fetch_account_integrations
    account_integrations_generation: AccountIntegrationsGenerationFn = (
        _default_account_integrations_generation
    )

    def install(self) -> None:
        """Bind these as the process-wide resolution adapters."""
        global _installed_adapters
        _installed_adapters = self


@dataclass(frozen=True)
class RemoteIntegrationsProvider:
    """How org integrations are fetched from a remote, installed once at the surface."""

    fetch: RemoteIntegrationsFetcher = _default_fetch_remote

    def install(self) -> None:
        """Bind this as the process-wide remote fetcher."""
        global _installed_remote
        _installed_remote = self


@dataclass(frozen=True)
class IntegrationSetupCommand:
    """How a surface spells the connect command, installed once at boot."""

    render: Callable[[str], str] = _default_integration_setup_command

    def install(self) -> None:
        """Bind this as the process-wide setup-command renderer."""
        global _installed_setup
        _installed_setup = self


_installed_adapters: IntegrationResolutionAdapters | None = None
_installed_remote: RemoteIntegrationsProvider | None = None
_installed_setup: IntegrationSetupCommand | None = None

_DEFAULT_ADAPTERS = IntegrationResolutionAdapters()


def _adapters() -> IntegrationResolutionAdapters:
    return _installed_adapters if _installed_adapters is not None else _DEFAULT_ADAPTERS


def fetch_remote_integrations(*, org_id: str, auth_token: str) -> list[dict[str, Any]]:
    fetch = _installed_remote.fetch if _installed_remote is not None else _default_fetch_remote
    return fetch(org_id, auth_token)


def configured_integration_services() -> tuple[str, ...]:
    return _adapters().configured_services()


def integration_sources_stamp() -> tuple[int, int]:
    """A value that changes when any integration source behind a session changes.

    Combines the local store file's stamp with the signed-in account's
    remote-set generation, so a credential saved either locally or in the web
    app invalidates a session's resolved cache on its next turn.
    """
    return (integrations_store_stamp(), _adapters().account_integrations_generation())


def setupable_integration_services() -> tuple[str, ...]:
    """Service ids that have a real setup handler (never invent outside this set)."""
    return _adapters().setupable_services()


def integration_setup_command(service_id: str) -> str:
    """Return the surface command that connects ``service_id``."""
    render = (
        _installed_setup.render
        if _installed_setup is not None
        else _default_integration_setup_command
    )
    return render(service_id)


class IntegrationResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    resolved_integrations: dict[str, Any] | None = None
    auth_token: str = Field(default="", alias="_auth_token")
    org_id: str = ""
    github_connection_id: str | None = None

    @field_validator("auth_token", "org_id", mode="before")
    @classmethod
    def _coerce_optional_string(cls, value: Any) -> str:
        return str(value or "").strip()


class IntegrationResolutionResult(StrictConfigModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resolved_integrations: dict[str, Any] = Field(default_factory=dict)
    progress_message: str | None = None

    @property
    def services(self) -> tuple[str, ...]:
        return tuple(
            service for service in self.resolved_integrations if not service.startswith("_")
        )


def resolve_integrations(state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return resolve_integrations_with_metadata(state).resolved_integrations


def select_github_connection(resolved: dict[str, Any], connection_id: str | None) -> dict[str, Any]:
    """Apply the connection choice to an already resolved integration snapshot."""
    return _adapters().select_github_connection(resolved, connection_id)


def resolve_integrations_with_metadata(
    state: Mapping[str, Any] | None = None,
) -> IntegrationResolutionResult:
    from infrastructure.harness_providers.integration_selection import current_github_connection_id

    request = IntegrationResolutionRequest.model_validate(state or {})
    result = _resolve_integrations_request(request)
    connection_id = (
        request.github_connection_id
        if state and "github_connection_id" in state
        else current_github_connection_id()
    )
    selected = select_github_connection(result.resolved_integrations, connection_id)
    return result.model_copy(update={"resolved_integrations": selected})


def _resolve_integrations_request(
    request: IntegrationResolutionRequest,
) -> IntegrationResolutionResult:
    existing = request.resolved_integrations
    if existing:
        return IntegrationResolutionResult(resolved_integrations=dict(existing))

    org_id = request.org_id
    auth_token = _strip_bearer(request.auth_token)

    if auth_token:
        if not org_id:
            org_id = _decode_org_id_from_token(auth_token)
        if not org_id:
            logger.warning("_auth_token present but could not decode org_id")
            return IntegrationResolutionResult()
        try:
            all_integrations = fetch_remote_integrations(org_id=org_id, auth_token=auth_token)
        except Exception as exc:
            logger.warning("Remote integrations fetch failed: %s", exc)
            return IntegrationResolutionResult()
        resolved = _adapters().classify_integrations(all_integrations)
        return IntegrationResolutionResult(
            resolved_integrations=resolved,
            progress_message=_resolved_message(resolved),
        )

    env_token = _strip_bearer(os.getenv("JWT_TOKEN", "").strip())
    if env_token:
        if not org_id:
            org_id = _decode_org_id_from_token(env_token)
        if not org_id:
            return _resolve_from_webapp_vault_or_local()
        try:
            all_integrations = fetch_remote_integrations(org_id=org_id, auth_token=env_token)
        except Exception:
            logger.debug(
                "Remote integrations fetch failed for org %s, falling back to local",
                org_id,
                exc_info=True,
            )
            return _resolve_from_webapp_vault_or_local()
        return _resolve_remote_with_local_fallback(all_integrations)

    return _resolve_from_webapp_vault_or_local()


def _resolve_from_webapp_vault_or_local() -> IntegrationResolutionResult:
    """Silo path: pull org vault from opensre-webapp, else account/local sources.

    Merge order is vault → store → env so ops can still override a vault
    secret with ``GITHUB_MCP_AUTH_TOKEN`` (etc.) on the task definition.
    On a signed-in laptop (no fleet vault) the account's organization
    integrations fill the remote role instead, and there they win over the
    local store and env.
    """
    adapters = _adapters()
    remote = adapters.fetch_webapp_vault()
    if not remote:
        # A configured fleet vault that failed, or an org with nothing exported,
        # stays on this silo. The signed-in account is only for a laptop that
        # has no fleet vault; that account can belong to a different organization.
        if adapters.fleet_vault_configured():
            return _resolve_from_local_sources()
        account_records = adapters.fetch_account_integrations()
        if account_records:
            return _resolve_remote_with_local_fallback(account_records)
        return _resolve_from_local_sources()

    store_integrations = adapters.load_integrations()
    env_integrations = adapters.load_env_integrations()
    integrations = adapters.merge_integrations_by_service(
        remote,
        store_integrations,
        env_integrations,
    )
    resolved = adapters.classify_integrations(integrations)
    services = [service for service in resolved if not service.startswith("_")]
    return IntegrationResolutionResult(
        resolved_integrations=resolved,
        progress_message=(
            f"Resolved integrations from webapp vault"
            f"{', store' if store_integrations else ''}"
            f"{', env' if env_integrations else ''}: {services}"
            if services
            else "No active integrations found"
        ),
    )


def _resolved_message(resolved: dict[str, Any]) -> str:
    services = [service for service in resolved if not service.startswith("_")]
    return f"Resolved integrations: {services}" if services else "No active integrations found"


def _resolve_from_local_sources() -> IntegrationResolutionResult:
    adapters = _adapters()
    store_integrations = adapters.load_integrations()
    env_integrations = adapters.load_env_integrations() if not store_integrations else []
    integrations = adapters.merge_local_integrations(store_integrations, env_integrations)
    if not integrations:
        return IntegrationResolutionResult(
            resolved_integrations={},
            progress_message=(
                f"No auth context and no local integrations found "
                f"(store: {adapters.integration_store_path()}, env fallback checked)"
            ),
        )

    resolved = adapters.classify_integrations(integrations)
    services = [service for service in resolved if not service.startswith("_")]
    source_labels: list[str] = []
    if store_integrations:
        source_labels.append("store")
    if env_integrations:
        source_labels.append("env")
    return IntegrationResolutionResult(
        resolved_integrations=resolved,
        progress_message=(
            f"Resolved local integrations from {', '.join(source_labels)}: {services}"
            if source_labels
            else f"Resolved local integrations: {services}"
        ),
    )


def _resolve_remote_with_local_fallback(
    remote_integrations: list[dict[str, Any]],
) -> IntegrationResolutionResult:
    adapters = _adapters()
    store_integrations = adapters.load_integrations()
    env_integrations = adapters.load_env_integrations()
    integrations = adapters.merge_integrations_by_service(
        env_integrations,
        store_integrations,
        remote_integrations,
    )
    resolved = adapters.classify_integrations(integrations)
    services = [service for service in resolved if not service.startswith("_")]

    source_labels = ["remote"]
    if store_integrations:
        source_labels.append("store")
    if env_integrations:
        source_labels.append("env")

    return IntegrationResolutionResult(
        resolved_integrations=resolved,
        progress_message=(
            f"Resolved integrations from {', '.join(source_labels)}: {services}"
            if services
            else "No active integrations found"
        ),
    )


def _decode_org_id_from_token(token: str) -> str:
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        return claims.get("organization") or claims.get("org_id") or ""
    except Exception:
        logger.debug("Failed to decode org_id from JWT token", exc_info=True)
        return ""


def _strip_bearer(token: str) -> str:
    if token.lower().startswith("bearer "):
        return token.split(None, 1)[1].strip()
    return token


def reset() -> None:
    """Clear all installed integration-resolution holders (tests)."""
    global _installed_adapters, _installed_remote, _installed_setup
    _installed_adapters = None
    _installed_remote = None
    _installed_setup = None


__all__ = [
    "AccountIntegrationsFetcherFn",
    "AccountIntegrationsGenerationFn",
    "ClassifyIntegrationsFn",
    "ConfiguredIntegrationServicesFn",
    "IntegrationResolutionAdapters",
    "IntegrationResolutionRequest",
    "IntegrationResolutionResult",
    "IntegrationSetupCommand",
    "IntegrationStorePathFn",
    "LoadEnvIntegrationsFn",
    "LoadIntegrationsFn",
    "MergeIntegrationsByServiceFn",
    "MergeLocalIntegrationsFn",
    "RemoteIntegrationsFetcher",
    "RemoteIntegrationsProvider",
    "SetupableIntegrationServicesFn",
    "WebappVaultFetcherFn",
    "configured_integration_services",
    "fetch_remote_integrations",
    "integration_setup_command",
    "integration_sources_stamp",
    "resolve_integrations",
    "resolve_integrations_with_metadata",
    "select_github_connection",
    "setupable_integration_services",
]
