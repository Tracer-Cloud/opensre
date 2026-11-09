"""ServiceNow integration verifier — URL safety check plus a live table probe.

The CLI used to call :func:`validate_https_or_loopback_http_url` before storing
credentials while this verifier only checked presence. Migrating onto the
shared setup flow would have dropped that SSRF-style guard, so the URL check
and the authenticated ``sys_user`` probe now live here as the single source of
truth for both ``opensre integrations setup`` and the onboarding wizard.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import httpx

from infrastructure.text.url_validation import validate_https_or_loopback_http_url
from integrations.verification import register_validation_verifier


@dataclass(frozen=True)
class ServiceNowValidationResult:
    """Outcome of validating ServiceNow credentials against the instance."""

    ok: bool
    detail: str
    instance_url: str = ""


def build_servicenow_config(raw: dict[str, Any] | None) -> dict[str, str]:
    """Normalize a credential mapping into the probe's expected keys."""
    payload = raw or {}
    return {
        "instance_url": str(payload.get("instance_url") or payload.get("url") or "").strip(),
        "username": str(payload.get("username") or "").strip(),
        "password": str(payload.get("password") or ""),
    }


def validate_servicenow_config(config: dict[str, str]) -> ServiceNowValidationResult:
    """Refuse unsafe URLs, then probe ``sys_user`` with HTTP Basic auth."""
    instance_url = config.get("instance_url", "").strip()
    username = config.get("username", "").strip()
    password = config.get("password", "")
    if not instance_url or not username or not password:
        return ServiceNowValidationResult(
            ok=False, detail="Missing instance_url, username, or password."
        )
    try:
        base_url = validate_https_or_loopback_http_url(
            instance_url.rstrip("/"),
            service_name="ServiceNow",
            field_name="instance URL",
        )
    except ValueError as err:
        return ServiceNowValidationResult(ok=False, detail=str(err))

    try:
        resp = httpx.get(
            f"{base_url}/api/now/table/sys_user",
            params={"sysparm_limit": 1, "sysparm_fields": "user_name"},
            auth=(username, password),
            headers={"Accept": "application/json"},
            timeout=10,
        )
    except Exception as err:
        return ServiceNowValidationResult(ok=False, detail=f"ServiceNow validation failed: {err}")

    if resp.status_code == HTTPStatus.OK:
        return ServiceNowValidationResult(
            ok=True,
            detail=f"ServiceNow connected as {username} at {base_url}.",
            instance_url=base_url,
        )
    if resp.status_code == HTTPStatus.UNAUTHORIZED:
        return ServiceNowValidationResult(
            ok=False, detail="ServiceNow credentials invalid. Check username and password."
        )
    if resp.status_code == HTTPStatus.FORBIDDEN:
        return ServiceNowValidationResult(
            ok=False,
            detail=(
                "ServiceNow authenticated but the user cannot read the sys_user table. "
                "Grant a role with table read access (e.g. itil)."
            ),
        )
    if resp.status_code == HTTPStatus.NOT_FOUND:
        return ServiceNowValidationResult(
            ok=False, detail="ServiceNow instance URL not found. Check the URL."
        )
    return ServiceNowValidationResult(
        ok=False, detail=f"ServiceNow returned unexpected status {resp.status_code}."
    )


verify_servicenow = register_validation_verifier(
    "servicenow",
    build_config=build_servicenow_config,
    validate_config=validate_servicenow_config,
)

__all__ = [
    "ServiceNowValidationResult",
    "build_servicenow_config",
    "validate_servicenow_config",
    "verify_servicenow",
]
