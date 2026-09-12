"""Owner-only, non-secret metadata for a personal OpenSRE account."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit, urlunsplit

from filelock import FileLock

from config.account_validation import AccountValidationState, validate_account_session
from config.constants.account import (
    OPENSRE_ACCOUNT_FILENAME,
    OPENSRE_ACCOUNT_LLM_BASE_PATH,
    OPENSRE_ACCOUNT_METADATA_PATH_ENV,
    OPENSRE_ACCOUNT_TOKEN_ENV,
    OPENSRE_APP_URL_DEFAULT,
    OPENSRE_APP_URL_ENV,
)
from config.constants.paths import host_home
from config.secrets.store import (
    delete_secret,
    resolve_secret,
    resolve_stored_secret,
    save_secret,
)

_VERSION = 1
_LOCK_TIMEOUT_SECONDS = 10.0
_ACCOUNT_ROUTE_VALIDATION_TTL_SECONDS = 60.0


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
    llm_model: str = "gpt-5.4-mini"


@dataclass(frozen=True)
class AccountLLMRoute:
    """Hosted OpenAI route activated by a complete local account login."""

    base_url: str
    model: str


@dataclass(frozen=True)
class _AccountRouteCacheEntry:
    """Short-lived result of validating the hosted LLM route."""

    key: tuple[AccountRecord, bytes]
    checked_at: float
    route: AccountLLMRoute | None


_account_route_cache: _AccountRouteCacheEntry | None = None


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


def _clear_account_route_validation_cache() -> None:
    global _account_route_cache
    _account_route_cache = None


def save_account_record(record: AccountRecord) -> None:
    """Atomically persist non-secret account metadata with mode ``0600``."""
    path = account_metadata_path()
    _ensure_parent(path)
    with FileLock(str(_lock_path(path)), timeout=_LOCK_TIMEOUT_SECONDS):
        _write_record(path, record)
    _clear_account_route_validation_cache()


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
    if path.exists():
        with FileLock(str(_lock_path(path)), timeout=_LOCK_TIMEOUT_SECONDS):
            path.unlink(missing_ok=True)
    _clear_account_route_validation_cache()


def resolve_account_token() -> str:
    """Resolve the OpenSRE account bearer token without exposing its source."""
    return resolve_secret(OPENSRE_ACCOUNT_TOKEN_ENV)


def stored_account_token() -> str:
    """Return the file-stored account token, ignoring any environment override."""
    return resolve_stored_secret(OPENSRE_ACCOUNT_TOKEN_ENV)


def save_account_token(value: str) -> None:
    """Persist the OpenSRE account bearer token in owner-only credential storage."""
    save_secret(OPENSRE_ACCOUNT_TOKEN_ENV, value)
    _clear_account_route_validation_cache()


def delete_account_token() -> None:
    """Delete the locally persisted OpenSRE account bearer token."""
    delete_secret(OPENSRE_ACCOUNT_TOKEN_ENV)
    _clear_account_route_validation_cache()


def _validated_account_llm_route(record: AccountRecord, token: str) -> AccountLLMRoute | None:
    try:
        app_url = normalize_account_app_url(record.app_url)
    except ValueError:
        return None

    validation = validate_account_session(
        app_url=app_url,
        token=token,
        expected_user_id=record.user_id,
        expected_organization_id=record.organization_id,
    )
    if validation.state is not AccountValidationState.ACTIVE or validation.llm_model is None:
        return None
    return AccountLLMRoute(
        base_url=f"{app_url}{OPENSRE_ACCOUNT_LLM_BASE_PATH}",
        model=validation.llm_model,
    )


def account_llm_route() -> AccountLLMRoute | None:
    """Return the hosted route only while the stored account session validates."""
    record = load_account_record()
    token = resolve_account_token()
    if record is None or record.llm_provider != "openai" or not token:
        return None

    token_fingerprint = hashlib.sha256(token.encode("utf-8")).digest()
    key = (record, token_fingerprint)
    now = monotonic()
    cached = _account_route_cache
    if (
        cached is not None
        and cached.key == key
        and now - cached.checked_at < _ACCOUNT_ROUTE_VALIDATION_TTL_SECONDS
    ):
        return cached.route

    route = _validated_account_llm_route(record, token)
    globals()["_account_route_cache"] = _AccountRouteCacheEntry(
        key=key,
        checked_at=now,
        route=route,
    )
    return route


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
