"""GitHub-only browser authentication for a local OpenSRE installation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import queue
import secrets
import time
import webbrowser
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from config.account import (
    AccountRecord,
    delete_account_record,
    delete_account_token,
    load_account_record,
    normalize_account_app_url,
    save_account_record,
    save_account_token,
    stored_account_token,
)
from config.constants.account import (
    OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
    OPENSRE_ACCOUNT_SESSION_PATH,
    OPENSRE_ACCOUNT_TOKEN_ENV,
)
from config.constants.github import GITHUB_CLI_REQUIRED_SCOPES
from integrations.github import (
    PersonalGitHubSnapshot,
    configure_personal_github,
    disconnect_personal_github,
    restore_personal_github,
)

_LOGIN_PATH = "/cli/auth/github"
_EXCHANGE_PATH = "/api/auth/github/cli/exchange"
_SUCCESS_PATH = "/cli/auth/github/success"


class AccountAuthError(RuntimeError):
    """Personal account authentication could not complete safely."""


class LoginProgress(Protocol):
    """User-facing progress for GitHub account login."""

    def prompt_sign_in(self, url: str, *, opened: bool) -> None:
        """Show the sign-in URL, numbered browser steps, and wait state."""

    def authorization_received(self) -> None:
        """Show that the loopback callback arrived and setup continues."""

    def setup_complete(self) -> None:
        """Show that GitHub integration and the hosted model are ready."""


class _SilentLoginProgress:
    def prompt_sign_in(self, url: str, *, opened: bool) -> None:
        _ = (url, opened)

    def authorization_received(self) -> None:
        return

    def setup_complete(self) -> None:
        return


@dataclass(frozen=True)
class AccountLoginResult:
    """Successful account login and the GitHub scopes it supplied."""

    record: AccountRecord
    warning: str = ""


@dataclass(frozen=True)
class AccountLogoutResult:
    """Result of clearing remote and local personal-account credentials."""

    remote_revoked: bool
    detail: str


@dataclass(frozen=True)
class _CallbackResult:
    code: str = ""
    error: str = ""


@dataclass(frozen=True)
class _ExchangeResult:
    access_token: str
    token_expires_at: str
    user_id: str
    organization_id: str
    github_username: str
    github_access_token: str
    github_scopes: tuple[str, ...]
    llm_provider: str
    llm_model: str
    email: str | None


def normalize_app_url(value: str | None = None) -> str:
    """Resolve and validate the webapp origin used for account authentication."""
    try:
        return normalize_account_app_url(value)
    except ValueError as exc:
        raise AccountAuthError(str(exc)) from exc


def _app_endpoint(app_url: str, path: str) -> str:
    return f"{app_url}{path}"


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _login_url(app_url: str, *, callback_port: int, state: str, code_challenge: str) -> str:
    query = urlencode(
        {
            "callback_port": callback_port,
            "state": state,
            "code_challenge": code_challenge,
        }
    )
    return f"{_app_endpoint(app_url, _LOGIN_PATH)}?{query}"


class _LoopbackCallback(BaseHTTPRequestHandler):
    def __init__(
        self,
        *args: Any,
        expected_state: str,
        success_url: str,
        results: queue.Queue[_CallbackResult],
        **kwargs: Any,
    ) -> None:
        self._expected_state = expected_state
        self._success_url = success_url
        self._results = results
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        parsed = urlsplit(self.path)
        if parsed.path != "/callback":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        query = parse_qs(parsed.query, keep_blank_values=True)
        state = query.get("state", [""])[0]
        if not hmac.compare_digest(state, self._expected_state):
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid login state")
            return

        error = query.get("error", [""])[0]
        code = query.get("code", [""])[0]
        if error or not code:
            self._results.put(_CallbackResult(error=error or "missing authorization code"))
            self.send_error(HTTPStatus.BAD_REQUEST, "OpenSRE login was not completed")
            return

        self._results.put(_CallbackResult(code=code))
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", self._success_url)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def _wait_for_callback(
    server: HTTPServer,
    results: queue.Queue[_CallbackResult],
    *,
    timeout_seconds: float,
) -> _CallbackResult:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        server.timeout = min(1.0, max(0.01, remaining))
        server.handle_request()
        try:
            return results.get_nowait()
        except queue.Empty:
            continue
    raise AccountAuthError("Timed out waiting for GitHub sign-in to finish.")


def _required_string(value: Mapping[str, object], key: str) -> str:
    resolved = value.get(key)
    if not isinstance(resolved, str) or not resolved.strip():
        raise AccountAuthError("The OpenSRE app returned an invalid login response.")
    return resolved.strip()


def _mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    resolved = value.get(key)
    if not isinstance(resolved, Mapping):
        raise AccountAuthError("The OpenSRE app returned an invalid login response.")
    return resolved


def _decode_exchange(payload: object) -> _ExchangeResult:
    if not isinstance(payload, Mapping):
        raise AccountAuthError("The OpenSRE app returned an invalid login response.")
    user = _mapping(payload, "user")
    organization = _mapping(payload, "organization")
    github = _mapping(payload, "github")
    llm = _mapping(payload, "llm")
    raw_email = user.get("email")
    if raw_email is not None and not isinstance(raw_email, str):
        raise AccountAuthError("The OpenSRE app returned an invalid login response.")
    raw_scopes = github.get("scopes", [])
    if not isinstance(raw_scopes, list) or not all(isinstance(scope, str) for scope in raw_scopes):
        raise AccountAuthError("The OpenSRE app returned invalid GitHub scopes.")
    github_scopes = tuple(sorted(set(raw_scopes)))
    missing_scopes = sorted(GITHUB_CLI_REQUIRED_SCOPES.difference(github_scopes))
    if missing_scopes:
        raise AccountAuthError(
            "The GitHub integration is missing required access: "
            + ", ".join(missing_scopes)
            + ". Run account login again and approve the requested permissions."
        )
    llm_provider = _required_string(llm, "provider").lower()
    if llm_provider != "openai":
        raise AccountAuthError("The OpenSRE app returned an unsupported LLM provider.")
    return _ExchangeResult(
        access_token=_required_string(payload, "access_token"),
        token_expires_at=_required_string(payload, "expires_at"),
        user_id=_required_string(user, "id"),
        organization_id=_required_string(organization, "id"),
        github_username=_required_string(github, "username"),
        github_access_token=_required_string(github, "access_token"),
        github_scopes=github_scopes,
        llm_provider=llm_provider,
        llm_model=_required_string(llm, "model"),
        email=raw_email.strip() if isinstance(raw_email, str) and raw_email.strip() else None,
    )


def _exchange_code(app_url: str, code: str, verifier: str) -> _ExchangeResult:
    try:
        response = httpx.post(
            _app_endpoint(app_url, _EXCHANGE_PATH),
            json={"code": code, "code_verifier": verifier},
            headers={"Accept": "application/json"},
            timeout=OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return _decode_exchange(response.json())
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise AccountAuthError("The OpenSRE app could not exchange the login code.") from exc


def _revoke_remote(app_url: str, token: str) -> bool:
    try:
        response = httpx.delete(
            _app_endpoint(app_url, OPENSRE_ACCOUNT_SESSION_PATH),
            headers={"Authorization": f"Bearer {token}"},
            timeout=OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
        )
        return response.status_code in {HTTPStatus.NO_CONTENT, HTTPStatus.UNAUTHORIZED}
    except httpx.HTTPError:
        return False


def _restore_previous_account(
    record: AccountRecord | None, token: str, github: PersonalGitHubSnapshot
) -> None:
    restore_personal_github(github)
    if token:
        save_account_token(token)
    else:
        delete_account_token()
    if record:
        save_account_record(record)
    else:
        delete_account_record()


def _cleanup_failed_login(
    app_url: str,
    access_token: str,
    *,
    previous_record: AccountRecord | None,
    previous_token: str,
    github_snapshot: PersonalGitHubSnapshot,
) -> None:
    _revoke_remote(app_url, access_token)
    with suppress(Exception):
        _restore_previous_account(previous_record, previous_token, github_snapshot)


def login_account(
    *,
    app_url: str | None = None,
    open_browser: bool = True,
    timeout_seconds: float = 300.0,
    progress: LoginProgress | None = None,
    browser_open: Callable[[str], bool] = webbrowser.open,
) -> AccountLoginResult:
    """Complete GitHub OAuth in a browser and persist the local account safely."""
    reporter = progress if progress is not None else _SilentLoginProgress()
    if timeout_seconds <= 0:
        raise AccountAuthError("Login timeout must be greater than zero.")
    resolved_app_url = normalize_app_url(app_url)
    state = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()
    results: queue.Queue[_CallbackResult] = queue.Queue(maxsize=1)
    server = HTTPServer(
        ("127.0.0.1", 0),
        partial(
            _LoopbackCallback,
            expected_state=state,
            success_url=_app_endpoint(resolved_app_url, _SUCCESS_PATH),
            results=results,
        ),
    )
    try:
        callback_port = int(server.server_address[1])
        authorization_url = _login_url(
            resolved_app_url,
            callback_port=callback_port,
            state=state,
            code_challenge=challenge,
        )
        opened = bool(open_browser and browser_open(authorization_url))
        reporter.prompt_sign_in(authorization_url, opened=opened)
        callback = _wait_for_callback(server, results, timeout_seconds=timeout_seconds)
    finally:
        server.server_close()

    if callback.error:
        raise AccountAuthError(f"GitHub sign-in failed: {callback.error}")

    reporter.authorization_received()
    exchange = _exchange_code(resolved_app_url, callback.code, verifier)
    previous_record = load_account_record()
    previous_token = stored_account_token()
    record = AccountRecord(
        user_id=exchange.user_id,
        organization_id=exchange.organization_id,
        github_username=exchange.github_username,
        email=exchange.email,
        app_url=resolved_app_url,
        signed_in_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        token_expires_at=exchange.token_expires_at,
        llm_provider=exchange.llm_provider,
        llm_model=exchange.llm_model,
        github_scopes=exchange.github_scopes,
    )
    try:
        github_snapshot = configure_personal_github(
            access_token=exchange.github_access_token,
            username=exchange.github_username,
        )
    except Exception as exc:
        _revoke_remote(resolved_app_url, exchange.access_token)
        raise AccountAuthError("OpenSRE could not safely persist the GitHub login.") from exc
    try:
        save_account_token(exchange.access_token)
        save_account_record(record)
    except Exception as exc:
        _cleanup_failed_login(
            resolved_app_url,
            exchange.access_token,
            previous_record=previous_record,
            previous_token=previous_token,
            github_snapshot=github_snapshot,
        )
        raise AccountAuthError("OpenSRE could not safely persist the login.") from exc
    from core.llm.factory import reset_llm_clients

    reset_llm_clients()
    reporter.setup_complete()
    if previous_token and previous_token != exchange.access_token:
        previous_app_url = previous_record.app_url if previous_record else resolved_app_url
        _revoke_remote(previous_app_url, previous_token)
    env_token = os.getenv(OPENSRE_ACCOUNT_TOKEN_ENV, "").strip()
    warning = ""
    if env_token and env_token != exchange.access_token:
        warning = (
            f"{OPENSRE_ACCOUNT_TOKEN_ENV} is set in your environment and will keep "
            "overriding the token just saved. Unset it so this login is used."
        )
    return AccountLoginResult(record=record, warning=warning)


def logout_account() -> AccountLogoutResult:
    """Revoke the remote token and remove account-managed local credentials."""
    record = load_account_record()
    token = stored_account_token()
    app_url = record.app_url if record else normalize_app_url()
    remote_revoked = not token or _revoke_remote(app_url, token)

    try:
        delete_account_token()
        if record:
            disconnect_personal_github()
        delete_account_record()
    except Exception as exc:
        raise AccountAuthError("OpenSRE could not clear all local account data.") from exc

    from core.llm.factory import reset_llm_clients

    reset_llm_clients()

    if os.getenv(OPENSRE_ACCOUNT_TOKEN_ENV, "").strip():
        return AccountLogoutResult(
            remote_revoked,
            f"Local files were cleared, but unset {OPENSRE_ACCOUNT_TOKEN_ENV} in your environment.",
        )
    detail = (
        "Signed out and removed local account credentials."
        if remote_revoked
        else "Local credentials were removed; remote revocation could not be confirmed."
    )
    return AccountLogoutResult(remote_revoked, detail)


__all__ = [
    "AccountAuthError",
    "AccountLoginResult",
    "AccountLogoutResult",
    "LoginProgress",
    "login_account",
    "logout_account",
    "normalize_app_url",
]
