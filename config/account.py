"""Owner-only, non-secret metadata for a personal OpenSRE account."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from filelock import FileLock

from config.constants.account import (
    OPENSRE_ACCOUNT_FILENAME,
    OPENSRE_ACCOUNT_LLM_BASE_PATH,
    OPENSRE_ACCOUNT_LLM_MODEL_ENV,
    OPENSRE_ACCOUNT_METADATA_PATH_ENV,
    OPENSRE_ACCOUNT_ROUTE_CACHE_SECONDS,
    OPENSRE_ACCOUNT_ROUTE_VALIDATION_TIMEOUT_SECONDS,
    OPENSRE_ACCOUNT_TOKEN_ENV,
    OPENSRE_APP_URL_DEFAULT,
    OPENSRE_APP_URL_ENV,
    OPENSRE_GATEWAY_LLM_MODEL_DEFAULT,
)
from config.constants.billing import WEBAPP_URL_ENV
from config.constants.paths import host_home
from config.secrets.store import (
    delete_secret,
    resolve_secret,
    resolve_stored_secret,
    save_secret,
)

_VERSION = 1
_DEFAULT_ACCOUNT_LLM_MODEL = "gpt-5.4-mini"
_LOCK_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class AccountRecord:
    """Non-secret identity associated with the local OpenSRE login."""

    user_id: str
    organization_id: str
    email: str | None
    app_url: str
    signed_in_at: str
    token_expires_at: str
    llm_provider: str = "openai"
    llm_model: str = _DEFAULT_ACCOUNT_LLM_MODEL


@dataclass(frozen=True)
class AccountLLMRoute:
    """Hosted OpenAI route activated by a complete local account login."""

    base_url: str
    model: str


def normalize_account_app_url(value: str | None = None) -> str:
    """Resolve a safe HTTP(S) origin for account authentication and validation."""
    raw = (value or os.getenv(OPENSRE_APP_URL_ENV) or OPENSRE_APP_URL_DEFAULT).strip()
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"Invalid OpenSRE app URL. Set {OPENSRE_APP_URL_ENV} to an http(s) origin."
        )
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def account_metadata_path() -> Path:
    """Return the host-owned account metadata path."""
    override = os.getenv(OPENSRE_ACCOUNT_METADATA_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return host_home() / OPENSRE_ACCOUNT_FILENAME


def _lock_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with suppress(OSError):
        path.parent.chmod(0o700)


def _write_record(path: Path, record: AccountRecord) -> None:
    _ensure_parent(path)
    payload = {"version": _VERSION, "account": asdict(record)}
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
        with suppress(OSError):
            path.chmod(0o600)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _parse_record(value: object) -> AccountRecord | None:
    if not isinstance(value, Mapping):
        return None
    required = (
        "user_id",
        "organization_id",
        "app_url",
        "signed_in_at",
        "token_expires_at",
    )
    if any(not isinstance(value.get(key), str) or not value[key] for key in required):
        return None
    email = value.get("email")
    if email is not None and not isinstance(email, str):
        return None
    llm_provider = value.get("llm_provider", "openai")
    llm_model = value.get("llm_model", "gpt-5.4-mini")
    if llm_provider != "openai" or not isinstance(llm_model, str) or not llm_model.strip():
        return None
    return AccountRecord(
        user_id=str(value["user_id"]),
        organization_id=str(value["organization_id"]),
        email=email,
        app_url=str(value["app_url"]),
        signed_in_at=str(value["signed_in_at"]),
        token_expires_at=str(value["token_expires_at"]),
        llm_provider=llm_provider,
        llm_model=llm_model.strip(),
    )


def save_account_record(record: AccountRecord) -> None:
    """Atomically persist non-secret account metadata with mode ``0600``."""
    path = account_metadata_path()
    _ensure_parent(path)
    with FileLock(str(_lock_path(path)), timeout=_LOCK_TIMEOUT_SECONDS):
        _write_record(path, record)
    _clear_account_route_session_cache()


def load_account_record() -> AccountRecord | None:
    """Load account metadata, returning ``None`` for absent or invalid data."""
    path = account_metadata_path()
    if not path.exists():
        return None
    with FileLock(str(_lock_path(path)), timeout=_LOCK_TIMEOUT_SECONDS):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    if not isinstance(payload, Mapping) or payload.get("version") != _VERSION:
        return None
    return _parse_record(payload.get("account"))


def delete_account_record() -> None:
    """Delete the local non-secret account record when present."""
    path = account_metadata_path()
    if not path.exists():
        return
    with FileLock(str(_lock_path(path)), timeout=_LOCK_TIMEOUT_SECONDS):
        path.unlink(missing_ok=True)
    _clear_account_route_session_cache()


def resolve_account_token() -> str:
    """Resolve the OpenSRE account bearer token without exposing its source."""
    return resolve_secret(OPENSRE_ACCOUNT_TOKEN_ENV)


def stored_account_token() -> str:
    """Return the file-stored account token, ignoring any environment override."""
    return resolve_stored_secret(OPENSRE_ACCOUNT_TOKEN_ENV)


def save_account_token(value: str) -> None:
    """Persist the OpenSRE account bearer token in owner-only credential storage."""
    save_secret(OPENSRE_ACCOUNT_TOKEN_ENV, value)


def delete_account_token() -> None:
    """Delete the locally persisted OpenSRE account bearer token."""
    delete_secret(OPENSRE_ACCOUNT_TOKEN_ENV)


def _token_fingerprint(token: str) -> str:
    """A stable, non-reversible cache key for a token without storing the token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _session_expired(record: AccountRecord) -> bool:
    """Whether the stored login is already past its recorded expiry."""
    raw = record.token_expires_at.strip()
    if not raw:
        return False
    try:
        expires_at = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


#: Last validation verdict per ``(app_url, token fingerprint)``; short-lived so a
#: revoked or unreachable login stops owning the route without a request per turn.
_ROUTE_SESSION_CACHE: dict[tuple[str, str], tuple[float, bool]] = {}
_ROUTE_SESSION_CACHE_LOCK = threading.Lock()


def _clear_account_route_session_cache() -> None:
    """Drop cached session verdicts (tests, and whenever login state changes)."""
    with _ROUTE_SESSION_CACHE_LOCK:
        _ROUTE_SESSION_CACHE.clear()


def _record_session_is_live(record: AccountRecord, token: str) -> bool:
    """Whether the stored login still validates, cached for a short window.

    ``account_llm_route`` runs on the LLM hot path, so validating with an HTTP
    request on every call would be too costly. A login past its recorded expiry
    is dropped without a request when the effective token is the one the record
    describes — a fresh ``OPENSRE_ACCOUNT_TOKEN`` override for the same account
    is still validated remotely. Everything else is validated with a short
    timeout and the verdict cached for ``OPENSRE_ACCOUNT_ROUTE_CACHE_SECONDS``.
    """
    try:
        app_url = normalize_account_app_url(record.app_url)
    except ValueError:
        return False
    if _session_expired(record) and token == stored_account_token():
        return False

    key = (app_url, _token_fingerprint(token))
    now = time.monotonic()
    with _ROUTE_SESSION_CACHE_LOCK:
        cached = _ROUTE_SESSION_CACHE.get(key)
        if cached is not None and now - cached[0] < OPENSRE_ACCOUNT_ROUTE_CACHE_SECONDS:
            return cached[1]

    from config.account_session import RemoteSessionState, validate_remote_session

    live = (
        validate_remote_session(
            app_url,
            token,
            timeout=OPENSRE_ACCOUNT_ROUTE_VALIDATION_TIMEOUT_SECONDS,
        )
        is RemoteSessionState.VALID
    )
    with _ROUTE_SESSION_CACHE_LOCK:
        _ROUTE_SESSION_CACHE[key] = (time.monotonic(), live)
    return live


def account_llm_route() -> AccountLLMRoute | None:
    """Return the hosted OpenAI route when this process holds a working login.

    A signed-in laptop has account metadata from ``opensre account login``, and
    that route is kept only while the stored login still validates — a revoked
    or unreachable session must not override a working local provider. A hosted
    gateway never logs in: the control plane injects its organization's token
    (``OPENSRE_ACCOUNT_TOKEN``) and the webapp URL, and that pair is the route
    the webapp meters against the token's organization.
    """
    token = resolve_account_token()
    if not token:
        return None
    record = load_account_record()
    if record is not None:
        if record.llm_provider != "openai":
            return None
        if not _record_session_is_live(record, token):
            return None
        app_url, model = record.app_url, record.llm_model
    else:
        app_url = os.getenv(WEBAPP_URL_ENV, "").strip()
        if not app_url:
            return None
        model = (
            os.getenv(OPENSRE_ACCOUNT_LLM_MODEL_ENV, "").strip()
            or OPENSRE_GATEWAY_LLM_MODEL_DEFAULT
        )
    return AccountLLMRoute(
        base_url=f"{app_url.rstrip('/')}{OPENSRE_ACCOUNT_LLM_BASE_PATH}",
        model=model,
    )


__all__ = [
    "AccountRecord",
    "AccountLLMRoute",
    "account_llm_route",
    "account_metadata_path",
    "delete_account_record",
    "delete_account_token",
    "load_account_record",
    "normalize_account_app_url",
    "resolve_account_token",
    "save_account_record",
    "save_account_token",
    "stored_account_token",
]
