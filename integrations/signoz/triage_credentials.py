"""Retain managed query authority across gateway replacement without cloning secrets."""

from __future__ import annotations

import os
from typing import Literal

from config.constants.organization import organization_id
from config.constants.paths import CONTEXT_ROOT_ENV
from config.llm_credentials import resolve_env_credential
from config.secrets.store import keyring_is_disabled
from core.domain.alerts.triage.models import TriageSource
from integrations.store import get_instances


def managed_credentials_required() -> bool:
    """Deployed and environment-only processes cannot depend on host-local copies."""
    return bool(organization_id() or os.getenv(CONTEXT_ROOT_ENV) or keyring_is_disabled())


def managed_query_reference(
    query_url: str, api_key: str, credential_env: str | None
) -> tuple[Literal["environment", "integration"], str, str]:
    """Bind an existing environment or exact hydrated SigNoz instance reference."""
    if credential_env and os.getenv(credential_env, "").strip() == api_key:
        return "environment", credential_env, ""
    for instance in get_instances("signoz"):
        credentials = instance["credentials"]
        if (
            str(credentials.get("url", "")).rstrip("/") == query_url.rstrip("/")
            and str(credentials.get("api_key", "")).strip() == api_key
            and instance["integration_id"]
        ):
            return "integration", instance["integration_id"], instance["name"]
    raise ValueError(
        "Use a managed SigNoz integration or --api-key-env available in the gateway environment"
    )


def resolve_source_key(source: TriageSource) -> str:
    """Resolve only the retained authority, rejecting changed integration destinations."""
    if source.credential_kind == "environment":
        return os.getenv(source.credential_ref, "").strip()
    if source.credential_kind == "owned":
        return resolve_env_credential(source.credential_ref)
    instances = [
        instance
        for instance in get_instances("signoz")
        if instance["integration_id"] == source.credential_ref
        and instance["name"] == source.credential_instance
    ]
    if len(instances) != 1:
        return ""
    credentials = instances[0]["credentials"]
    if str(credentials.get("url", "")).rstrip("/") != source.query_url.rstrip("/"):
        return ""
    return str(credentials.get("api_key", "")).strip()
