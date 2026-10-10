"""Operator-owned source setup without mutating live SigNoz alerts or paging."""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from urllib.parse import urlsplit

from config.constants.triage import TRIAGE_CREDENTIAL_PREFIX
from config.llm_credentials import delete_credential, save_credential
from core.domain.alerts.triage.models import TriageSource
from core.domain.alerts.triage.storage import TriageStore
from integrations.signoz.client import SigNozClient
from integrations.signoz.config import SigNozConfig


def validate_urls(query_url: str, ingress_url: str, *, demo: bool = False) -> None:
    """Require explicit HTTPS ingress for remote sources and reject URL secrets."""
    for name, url in (("Query URL", query_url), ("Gateway ingress", ingress_url)):
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                f"{name} must be an HTTP(S) URL without embedded credentials, query, or fragment"
            )
    ingress = urlsplit(ingress_url)
    local = ingress.hostname in {"localhost", "127.0.0.1", "::1"}
    if ingress.scheme != "https" and not (demo or local):
        raise ValueError("Remote live ingress requires your own HTTPS endpoint")


def connect_source(
    store: TriageStore,
    *,
    name: str,
    query_url: str,
    api_key: str,
    services: tuple[str, ...],
    ingress_url: str,
    demo: bool = False,
    source_id: str | None = None,
    webhook_password: str | None = None,
) -> tuple[TriageSource, str]:
    """Verify query access and create independent authenticated webhook credentials."""
    validate_urls(query_url, ingress_url, demo=demo)
    if not name.strip() or len(name) > 200 or not api_key.strip():
        raise ValueError("Source name and query-only API key are required")
    identifier = source_id or uuid.uuid4().hex
    if any(s.id == identifier for s in store.sources()):
        raise ValueError("Source ID already exists; reuse the existing connection")
    credential_ref = f"{TRIAGE_CREDENTIAL_PREFIX}{identifier.upper().replace('-', '_')}_API_KEY"
    password = webhook_password or secrets.token_urlsafe(32)
    source = TriageSource(
        id=identifier,
        name=name.strip(),
        query_url=query_url.rstrip("/"),
        credential_ref=credential_ref,
        services=services,
        webhook_url=f"{ingress_url.rstrip('/')}/alerts/signoz/{identifier}",
        username=identifier,
        password_digest=hashlib.sha256(password.encode()).hexdigest(),
        created_at=time.time(),
        demo=demo,
    )
    result = SigNozClient(SigNozConfig(url=source.query_url, api_key=api_key)).query_traces(
        service=source.services[0], limit=1
    )
    if not result.get("available"):
        raise ValueError(
            "SigNoz query access failed; verify the query URL and read-only service-account key"
        )
    source = source.model_copy(update={"query_ready": True})
    save_credential(credential_ref, api_key)
    try:
        store.add_source(source)
    except Exception:
        delete_credential(credential_ref)
        raise
    return source, password
