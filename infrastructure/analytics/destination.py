"""Resolve the first-party webapp destination for product analytics."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from config.account import (
    AccountRecord,
    account_metadata_path,
    load_account_record,
    normalize_account_app_url,
    resolve_account_token,
    stored_account_token,
)
from config.constants.account import (
    OPENSRE_ACCOUNT_TOKEN_ENV,
    OPENSRE_APP_URL_DEFAULT,
    OPENSRE_APP_URL_ENV,
)
from config.constants.analytics import (
    ANALYTICS_INGEST_PATH,
    ANALYTICS_SIGNATURE_HEADER,
    ANALYTICS_SIGNATURE_VERSION,
    ANALYTICS_TIMESTAMP_HEADER,
)
from config.constants.billing import USAGE_SECRET_ENV, WEBAPP_URL_ENV
from config.secrets.store import keyring_is_disabled


@dataclass(frozen=True, slots=True)
class AnalyticsDestination:
    """Resolved endpoint and optional bearer credential for one process."""

    endpoint_url: str
    bearer_token: str = field(default="", repr=False)

    def headers(
        self,
        body: bytes | None = None,
        *,
        timestamp: int | None = None,
    ) -> dict[str, str]:
        """Return auth and optional body-signature headers for one request."""
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
            if body is not None:
                signed_at = int(time.time()) if timestamp is None else timestamp
                signed = str(signed_at).encode("ascii") + b"." + body
                digest = hmac.new(
                    self.bearer_token.encode("utf-8"),
                    signed,
                    hashlib.sha256,
                ).hexdigest()
                headers[ANALYTICS_TIMESTAMP_HEADER] = str(signed_at)
                headers[ANALYTICS_SIGNATURE_HEADER] = f"{ANALYTICS_SIGNATURE_VERSION}={digest}"
        return headers


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _load_account() -> tuple[AccountRecord | None, bool]:
    """Return the account record and whether reading it failed."""
    try:
        path = account_metadata_path()
        metadata_present = path.exists() or path.is_symlink()
        record = load_account_record()
        if record is not None:
            return record, False
        metadata_present = metadata_present or path.exists() or path.is_symlink()
        return None, metadata_present
    except Exception:
        return None, True


def _normalize(value: str | None) -> str | None:
    try:
        normalized = normalize_account_app_url(value)
    except (OSError, ValueError):
        return None
    parsed = urlsplit(normalized)
    if parsed.scheme == "https":
        return normalized
    if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        return normalized
    return None


def _anonymous_base_url() -> str | None:
    explicit_url = _env(OPENSRE_APP_URL_ENV)
    if explicit_url:
        return _normalize(explicit_url)
    return OPENSRE_APP_URL_DEFAULT


def _account_token() -> str:
    try:
        return resolve_account_token().strip()
    except Exception:
        return ""


def _stored_account_token() -> str:
    try:
        return stored_account_token().strip()
    except Exception:
        return ""


def resolve_analytics_destination() -> AnalyticsDestination | None:
    """Resolve a safe destination, failing closed on explicit misconfiguration."""
    silo_url = _env(WEBAPP_URL_ENV)
    if silo_url:
        silo_base_url = _normalize(silo_url)
        bearer_token = _env(USAGE_SECRET_ENV)
        if silo_base_url is None or not bearer_token:
            return None
        base_url = silo_base_url
    else:
        environment_token = _env(OPENSRE_ACCOUNT_TOKEN_ENV)
        if environment_token and keyring_is_disabled():
            explicit_base_url = _env(OPENSRE_APP_URL_ENV)
            if not explicit_base_url:
                return None
            environment_base_url = _normalize(explicit_base_url)
            if environment_base_url is None:
                return None
            base_url = environment_base_url
            bearer_token = environment_token
        else:
            account, account_load_failed = _load_account()
            if account_load_failed:
                return None
            if account is not None:
                account_base_url = _normalize(account.app_url)
                bearer_token = _account_token()
                if account_base_url is None or not bearer_token:
                    return None
                if environment_token:
                    persisted_token = _stored_account_token()
                    if not persisted_token or not hmac.compare_digest(
                        environment_token,
                        persisted_token,
                    ):
                        return None
                base_url = account_base_url
            elif environment_token:
                return None
            else:
                anonymous_base_url = _anonymous_base_url()
                bearer_token = ""
                if anonymous_base_url is None:
                    return None
                base_url = anonymous_base_url
    return AnalyticsDestination(
        endpoint_url=f"{base_url.rstrip('/')}{ANALYTICS_INGEST_PATH}",
        bearer_token=bearer_token,
    )


__all__ = ["AnalyticsDestination", "resolve_analytics_destination"]
