"""Public integration catalog facade."""

from __future__ import annotations

import os
from typing import Any, cast

from config.constants.google_docs import (
    GOOGLE_CREDENTIALS_FILE_ENV,
    GOOGLE_DRIVE_FOLDER_ID_ENV,
)
from config.constants.helm import OSRE_HELM_INTEGRATION_ENV
from config.constants.new_relic import (
    NEW_RELIC_ACCOUNT_ID_ENV,
    NEW_RELIC_API_KEY_ENV,
    NEW_RELIC_INSTANCES_ENV,
)
from config.constants.yandex_cloud import (
    YC_FOLDER_ID_ENV,
    YC_IAM_TOKEN_ENV,
    YC_SA_KEY_ENV,
    YC_SA_KEY_FILE_ENV,
    YC_TOKEN_ENV,
    YC_USE_METADATA_ENV,
)
from integrations.registry import INTEGRATION_SPECS_BY_SERVICE, family_key, service_key
from integrations.store import load_integrations


def _load_catalog_impl() -> Any:
    """Load classifiers only when a caller actually classifies or merges."""
    from integrations import _catalog_impl as impl

    return impl


def _sync_overrides() -> None:
    """Keep monkeypatch-friendly facade attributes wired into the implementation module."""
    _load_catalog_impl().load_integrations = load_integrations


def classify_integrations(integrations: list[dict[str, Any]]) -> dict[str, Any]:
    _sync_overrides()
    return cast("dict[str, Any]", _load_catalog_impl().classify_integrations(integrations))


def load_env_integrations() -> list[dict[str, Any]]:
    _sync_overrides()
    return cast("list[dict[str, Any]]", _load_catalog_impl().load_env_integrations())


def merge_local_integrations(
    store_integrations: list[dict[str, Any]],
    env_integrations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    _sync_overrides()
    return cast(
        "list[dict[str, Any]]",
        _load_catalog_impl().merge_local_integrations(store_integrations, env_integrations),
    )


def merge_integrations_by_service(
    *integration_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    _sync_overrides()
    return cast(
        "list[dict[str, Any]]",
        _load_catalog_impl().merge_integrations_by_service(*integration_groups),
    )


def resolve_effective_integrations(
    store_integrations: list[dict[str, Any]] | None = None,
    env_integrations: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    _sync_overrides()
    return cast(
        "dict[str, dict[str, Any]]",
        _load_catalog_impl().resolve_effective_integrations(
            store_integrations=store_integrations,
            env_integrations=env_integrations,
        ),
    )


def _env_is_set(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def _any_env(*names: str) -> bool:
    return any(_env_is_set(name) for name in names)


def _all_env(*names: str) -> bool:
    return all(_env_is_set(name) for name in names)


def load_env_integration_services() -> list[str]:
    """Return integration services visible from plain env vars only.

    This is the startup-safe companion to :func:`load_env_integrations`: it must
    not resolve secrets from the OS keyring or build validated runtime configs.
    It exists for surfaces that only need to display configured service names
    before the first prompt.
    """
    services: list[str] = []

    def add(service: str, configured: bool) -> None:
        if configured:
            services.append(service)

    add(
        "grafana",
        _all_env("GRAFANA_INSTANCE_URL", "GRAFANA_READ_TOKEN") or _env_is_set("GRAFANA_INSTANCES"),
    )
    add("datadog", _all_env("DD_API_KEY", "DD_APP_KEY") or _env_is_set("DD_INSTANCES"))
    add(
        "groundcover",
        _any_env("GROUNDCOVER_API_KEY", "GROUNDCOVER_MCP_TOKEN", "GROUNDCOVER_INSTANCES"),
    )
    add("honeycomb", _any_env("HONEYCOMB_API_KEY", "HONEYCOMB_INSTANCES"))
    add("coralogix", _any_env("CORALOGIX_API_KEY", "CORALOGIX_INSTANCES"))
    add(
        "new_relic",
        _all_env(NEW_RELIC_API_KEY_ENV, NEW_RELIC_ACCOUNT_ID_ENV)
        or _env_is_set(NEW_RELIC_INSTANCES_ENV),
    )
    add(
        "aws",
        _any_env("AWS_INSTANCES", "AWS_ROLE_ARN")
        or _all_env("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"),
    )
    add(
        "github",
        (_env_is_set("GITHUB_MCP_COMMAND") and os.getenv("GITHUB_MCP_MODE", "").strip() == "stdio")
        or _env_is_set("GITHUB_MCP_URL"),
    )
    add("sentry", _all_env("SENTRY_ORG_SLUG", "SENTRY_AUTH_TOKEN"))
    add("gitlab", _env_is_set("GITLAB_ACCESS_TOKEN"))
    add(
        "google_docs",
        _all_env(GOOGLE_CREDENTIALS_FILE_ENV, GOOGLE_DRIVE_FOLDER_ID_ENV),
    )
    add("mongodb", _env_is_set("MONGODB_CONNECTION_STRING"))
    add(
        "argocd",
        _env_is_set("ARGOCD_INSTANCES")
        or (
            _env_is_set("ARGOCD_BASE_URL")
            and (
                _any_env("ARGOCD_AUTH_TOKEN", "ARGOCD_TOKEN")
                or _all_env("ARGOCD_USERNAME", "ARGOCD_PASSWORD")
            )
        ),
    )
    add("helm", os.getenv(OSRE_HELM_INTEGRATION_ENV, "").strip().lower() in {"1", "true", "yes"})
    add(
        "railway",
        _env_is_set("RAILWAY_TOKEN")
        or _all_env("RAILWAY_PROJECT", "RAILWAY_SERVICE", "RAILWAY_ENVIRONMENT"),
    )
    add("vercel", _env_is_set("VERCEL_API_TOKEN"))
    add("opsgenie", _env_is_set("OPSGENIE_API_KEY"))
    add("pagerduty", _env_is_set("PAGERDUTY_API_KEY"))
    add("incident_io", _env_is_set("INCIDENT_IO_API_KEY"))
    add("jira", _all_env("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"))
    add(
        "servicenow",
        _all_env("SERVICENOW_INSTANCE_URL", "SERVICENOW_USERNAME", "SERVICENOW_PASSWORD"),
    )
    add("discord", _env_is_set("DISCORD_BOT_TOKEN"))
    add("telegram", _env_is_set("TELEGRAM_BOT_TOKEN"))
    add(
        "rocketchat",
        _all_env("ROCKETCHAT_SERVER_URL", "ROCKETCHAT_AUTH_TOKEN", "ROCKETCHAT_USER_ID")
        or _env_is_set("ROCKETCHAT_WEBHOOK_URL"),
    )
    add("buzz", _env_is_set("BUZZ_PRIVATE_KEY"))
    add("slack", _any_env("SLACK_BOT_TOKEN", "SLACK_ACCESS_TOKEN"))
    add("smtp", _env_is_set("SMTP_HOST"))
    add("whatsapp", _all_env("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM"))
    add(
        "twilio",
        _all_env("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN")
        and _any_env("TWILIO_SMS_FROM", "TWILIO_SMS_MESSAGING_SERVICE_SID"),
    )
    add(
        "mongodb_atlas",
        _all_env(
            "MONGODB_ATLAS_PUBLIC_KEY", "MONGODB_ATLAS_PRIVATE_KEY", "MONGODB_ATLAS_PROJECT_ID"
        ),
    )
    add("posthog_mcp", _any_env("POSTHOG_MCP_COMMAND", "POSTHOG_MCP_URL", "POSTHOG_MCP_AUTH_TOKEN"))
    add("sentry_mcp", _any_env("SENTRY_MCP_COMMAND", "SENTRY_MCP_URL", "SENTRY_MCP_AUTH_TOKEN"))
    add("x_mcp", _any_env("X_MCP_COMMAND", "X_MCP_URL", "X_MCP_AUTH_TOKEN"))
    add("mariadb", _all_env("MARIADB_HOST", "MARIADB_DATABASE"))
    add("opensearch", _env_is_set("OPENSEARCH_URL"))
    add(
        "yandex_cloud",
        # A folder plus one credential, or the instance metadata service, which
        # needs neither. Mirrors the classifier's rule so the banner, health and
        # verification agree on whether it is configured.
        (
            _env_is_set(YC_FOLDER_ID_ENV)
            and _any_env(YC_SA_KEY_FILE_ENV, YC_SA_KEY_ENV, YC_TOKEN_ENV, YC_IAM_TOKEN_ENV)
        )
        or os.getenv(YC_USE_METADATA_ENV, "").strip().lower() in {"1", "true", "yes", "on"},
    )

    return list(dict.fromkeys(services))


def configured_integration_services() -> list[str]:
    """Return lowercase service keys for integrations configured via env or the local store.

    Single source of truth shared by the welcome banner and the REPL session so
    they never disagree about which integrations are connected. Covers both
    environment-variable configuration and integrations saved to ``~/.opensre``
    (e.g. via ``opensre integrations setup ...``). Never raises; returns an
    empty list on any failure so callers can treat it as best-effort.
    """
    try:
        store_records = load_integrations()
    except Exception:
        store_records = []
    return _configured_service_names(store_records=store_records)


def _configured_service_names(*, store_records: list[dict[str, Any]]) -> list[str]:
    """Merge env-visible and active store services into one deduplicated list."""
    services: list[str] = []

    try:
        env_services = load_env_integration_services()
    except Exception:
        env_services = []
    for service in env_services:
        service = str(service).strip().lower()
        if service:
            services.append(service)

    for record in store_records:
        if str(record.get("status", "active")).strip().lower() != "active":
            continue
        service = str(record.get("service", "")).strip().lower()
        if service and _is_registered_service(service):
            services.append(service)

    return list(dict.fromkeys(services))


def _is_registered_service(service: str) -> bool:
    """Return whether ``service`` still has a registered integration implementation."""
    return family_key(service_key(service)) in INTEGRATION_SPECS_BY_SERVICE


# Hosted MCP integrations that strictly require a personal API token when not
# running in ``stdio`` mode. A record carrying only a URL classifies as present
# but cannot connect, so callers must not imply it is working.
_HOSTED_MCP_TOKEN_REQUIRED: frozenset[str] = frozenset({"posthog_mcp", "sentry_mcp"})


def _hosted_mcp_missing_token(service: str, config: dict[str, Any]) -> bool:
    """Offline check for an obviously-unusable hosted MCP config.

    Hosted MCP servers (non-``stdio``) authenticate with a personal API token;
    a record with only a URL is "configured" but cannot connect. Mirrors the
    runtime-unavailable checks in the MCP integration modules without doing any
    network I/O.
    """
    if service not in _HOSTED_MCP_TOKEN_REQUIRED:
        return False
    if str(config.get("mode", "")).strip().lower() == "stdio":
        return False
    return not str(config.get("auth_token", "")).strip()


def configured_integration_health() -> list[tuple[str, str]]:
    """Return ``(service, status)`` for each configured integration.

    ``status`` is ``"ok"`` when the stored/env config is minimally complete
    enough to attempt a connection, or ``"incomplete"`` when required
    credentials are missing — for example a hosted MCP record saved without an
    API token, or a service whose secrets did not classify into a usable config.
    The welcome banner uses this so it reflects health rather than mere presence.

    Performs no network verification (startup stays fast) and never raises; on
    any failure each service falls back to ``"ok"`` so the banner still lists it.
    """
    try:
        store_records = load_integrations()
    except Exception:
        store_records = []

    services = _configured_service_names(store_records=store_records)
    if not services:
        return []

    store_config_by_service: dict[str, dict[str, Any]] = {}
    for record in store_records:
        if str(record.get("status", "active")).strip().lower() != "active":
            continue
        service = str(record.get("service", "")).strip().lower()
        if not service or not _is_registered_service(service):
            continue
        credentials = record.get("credentials")
        if isinstance(credentials, dict):
            store_config_by_service.setdefault(service, credentials)
            continue
        instances = record.get("instances")
        if isinstance(instances, list):
            for instance in instances:
                if not isinstance(instance, dict):
                    continue
                instance_credentials = instance.get("credentials")
                if isinstance(instance_credentials, dict):
                    store_config_by_service.setdefault(service, instance_credentials)
                    break

    health: list[tuple[str, str]] = []
    for service in services:
        config = store_config_by_service.get(service, {})
        if service == "posthog_mcp" and not config:
            config = {
                "mode": os.getenv("POSTHOG_MCP_MODE", "streamable-http").strip().lower(),
                "auth_token": os.getenv("POSTHOG_MCP_AUTH_TOKEN", "").strip(),
            }
        elif service == "sentry_mcp" and not config:
            config = {
                "mode": os.getenv("SENTRY_MCP_MODE", "streamable-http").strip().lower(),
                "auth_token": os.getenv("SENTRY_MCP_AUTH_TOKEN", "").strip(),
            }
        if _hosted_mcp_missing_token(service, config):
            health.append((service, "incomplete"))
            continue
        health.append((service, "ok"))
    return health


__all__ = [
    "classify_integrations",
    "configured_integration_health",
    "configured_integration_services",
    "load_env_integration_services",
    "load_env_integrations",
    "load_integrations",
    "merge_integrations_by_service",
    "merge_local_integrations",
    "resolve_effective_integrations",
]
