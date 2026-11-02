"""Browser authentication for a local OpenSRE installation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import queue
import re
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
    OPENSRE_ACCOUNT_EXCHANGE_PATH,
    OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
    OPENSRE_ACCOUNT_LOGIN_PASTE_ATTEMPTS,
    OPENSRE_ACCOUNT_LOGIN_PASTE_MODE,
    OPENSRE_ACCOUNT_LOGIN_PATH,
    OPENSRE_ACCOUNT_LOGIN_SUCCESS_PATH,
    OPENSRE_ACCOUNT_SESSION_PATH,
    OPENSRE_ACCOUNT_TOKEN_ENV,
    OPENSRE_ACCOUNT_USAGE_PATH,
)


class AccountAuthError(RuntimeError):
    """Personal account authentication could not complete safely."""


class _CodeRejectedError(AccountAuthError):
    """The app refused the code: expired, used, or bound to another sign-in."""


#: Shape of a webapp-issued authorization code, checked before a pasted value
#: is sent anywhere so a truncated paste is caught locally.
_AUTHORIZATION_CODE_PATTERN = re.compile(r"osre_code_[A-Za-z0-9_-]{43}")


class LoginProgress(Protocol):
    """User-facing progress and input for OpenSRE account login."""

    def prompt_sign_in(self, url: str, *, opened: bool) -> None:
        """Show the sign-in URL, numbered browser steps, and wait state."""

    def prompt_paste_sign_in(self, url: str) -> None:
        """Show the sign-in URL to open on any device and the paste-code steps."""

    def read_pasted_code(self) -> str:
        """Return one pasted line; raise ``EOFError`` or ``KeyboardInterrupt`` to cancel."""

    def pasted_code_rejected(self, detail: str) -> None:
        """Explain why the pasted code was not accepted before asking again."""

    def authorization_received(self) -> None:
        """Show that the browser authorization arrived and setup continues."""

    def setup_complete(self) -> None:
        """Show that the account and hosted model are ready."""


class _SilentLoginProgress:
    def prompt_sign_in(self, url: str, *, opened: bool) -> None:
        _ = (url, opened)

    def prompt_paste_sign_in(self, url: str) -> None:
        _ = url

    def read_pasted_code(self) -> str:
        raise AccountAuthError("Paste-code sign-in needs an interactive terminal.")

    def pasted_code_rejected(self, detail: str) -> None:
        _ = detail

    def authorization_received(self) -> None:
        return

    def setup_complete(self) -> None:
        return


@dataclass(frozen=True)
class AccountLoginResult:
    """Successful account login and any local warning it supplied."""

    record: AccountRecord
    warning: str = ""
    effective_token_matches_login: bool = True


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
    organization_id: str | None
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


def _login_url(
    app_url: str,
    *,
    callback_port: int | None,
    state: str,
    code_challenge: str,
    cli_auth_attempt_id: str | None = None,
) -> str:
    """Build the sign-in URL; no ``callback_port`` asks the app for paste mode."""
    delivery: dict[str, str | int] = (
        {"mode": OPENSRE_ACCOUNT_LOGIN_PASTE_MODE}
        if callback_port is None
        else {"callback_port": callback_port}
    )
    query = urlencode(
        {
            **delivery,
            "state": state,
            "code_challenge": code_challenge,
            **({"cli_auth_attempt_id": cli_auth_attempt_id} if cli_auth_attempt_id else {}),
        }
    )
    return f"{_app_endpoint(app_url, OPENSRE_ACCOUNT_LOGIN_PATH)}?{query}"


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
    raise AccountAuthError("Timed out waiting for OpenSRE sign-in to finish.")


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


def _organization_id(payload: Mapping[str, object]) -> str | None:
    """Read the optional active Clerk organization from an auth response."""
    if "organization" not in payload:
        raise AccountAuthError("The OpenSRE app returned an invalid login response.")
    organization = payload.get("organization")
    if organization is None:
        return None
    if not isinstance(organization, Mapping):
        raise AccountAuthError("The OpenSRE app returned an invalid login response.")
    return _required_string(organization, "id")


def _decode_exchange(payload: object) -> _ExchangeResult:
    if not isinstance(payload, Mapping):
        raise AccountAuthError("The OpenSRE app returned an invalid login response.")
    user = _mapping(payload, "user")
    llm = _mapping(payload, "llm")
    raw_email = user.get("email")
    if raw_email is not None and not isinstance(raw_email, str):
        raise AccountAuthError("The OpenSRE app returned an invalid login response.")
    llm_provider = _required_string(llm, "provider").lower()
    if llm_provider != "openai":
        raise AccountAuthError("The OpenSRE app returned an unsupported LLM provider.")
    return _ExchangeResult(
        access_token=_required_string(payload, "access_token"),
        token_expires_at=_required_string(payload, "expires_at"),
        user_id=_required_string(user, "id"),
        organization_id=_organization_id(payload),
        llm_provider=llm_provider,
        llm_model=_required_string(llm, "model"),
        email=raw_email.strip() if isinstance(raw_email, str) and raw_email.strip() else None,
    )


def _exchange_code(app_url: str, code: str, verifier: str) -> _ExchangeResult:
    try:
        response = httpx.post(
            _app_endpoint(app_url, OPENSRE_ACCOUNT_EXCHANGE_PATH),
            json={"code": code, "code_verifier": verifier},
            headers={"Accept": "application/json"},
            timeout=OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
        )
        if (
            response.status_code == HTTPStatus.BAD_REQUEST
            and _error_code(response) == "invalid_grant"
        ):
            raise _CodeRejectedError("The OpenSRE app did not accept the login code.")
        response.raise_for_status()
        return _decode_exchange(response.json())
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise AccountAuthError("The OpenSRE app could not exchange the login code.") from exc


def _error_code(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return ""
    error = payload.get("error") if isinstance(payload, Mapping) else None
    return error if isinstance(error, str) else ""


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


def _restore_previous_account(record: AccountRecord | None, token: str) -> None:
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
) -> None:
    _revoke_remote(app_url, access_token)
    with suppress(Exception):
        _restore_previous_account(previous_record, previous_token)


def usage_page_url(app_url: str | None = None) -> str:
    """The credits, usage and top-up page of the deployment the account signed in to.

    An explicit ``app_url`` wins; otherwise the URL saved with the login, so a
    custom deployment's account is not sent to the production site.
    """
    if not app_url:
        record = load_account_record()
        app_url = record.app_url if record is not None else None
    return _app_endpoint(normalize_app_url(app_url), OPENSRE_ACCOUNT_USAGE_PATH)


def open_usage_page(
    *,
    app_url: str | None = None,
    browser_open: Callable[[str], bool] | None = None,
) -> tuple[str, bool]:
    """Open the usage page in the browser; return ``(url, opened)``."""
    url = usage_page_url(app_url)
    opener = browser_open if browser_open is not None else webbrowser.open
    try:
        opened = bool(opener(url))
    except Exception:
        opened = False
    from infrastructure.analytics.capture import capture_browser_open_requested

    capture_browser_open_requested(target="account_usage", opened=opened)
    return url, opened


def _authorize_by_loopback(
    app_url: str,
    *,
    state: str,
    challenge: str,
    open_browser: bool,
    timeout_seconds: float,
    reporter: LoginProgress,
    browser_open: Callable[[str], bool],
) -> str:
    """Wait for the browser to redirect the code to this machine's loopback."""
    results: queue.Queue[_CallbackResult] = queue.Queue(maxsize=1)
    server = HTTPServer(
        ("127.0.0.1", 0),
        partial(
            _LoopbackCallback,
            expected_state=state,
            success_url=_app_endpoint(app_url, OPENSRE_ACCOUNT_LOGIN_SUCCESS_PATH),
            results=results,
        ),
    )
    try:
        callback_port = int(server.server_address[1])
        from infrastructure.analytics.capture import begin_cli_auth_attempt

        cli_auth_attempt_id = begin_cli_auth_attempt()
        authorization_url = _login_url(
            app_url,
            callback_port=callback_port,
            state=state,
            code_challenge=challenge,
            cli_auth_attempt_id=cli_auth_attempt_id,
        )
        opened = False
        if open_browser:
            try:
                opened = bool(browser_open(authorization_url))
            finally:
                from infrastructure.analytics.capture import capture_browser_open_requested

                capture_browser_open_requested(
                    target="account_login", opened=opened, cli_auth_attempt_id=cli_auth_attempt_id
                )
        reporter.prompt_sign_in(authorization_url, opened=opened)
        callback = _wait_for_callback(server, results, timeout_seconds=timeout_seconds)
    finally:
        server.server_close()

    if callback.error:
        raise AccountAuthError(f"OpenSRE sign-in failed: {callback.error}")
    return callback.code


def _authorize_by_paste(
    app_url: str,
    *,
    state: str,
    challenge: str,
    verifier: str,
    reporter: LoginProgress,
) -> _ExchangeResult:
    """Exchange a code the user copies from a browser on another machine.

    The code only works with this process's PKCE verifier, so one copied from
    someone else's sign-in, or shown to someone else, cannot be exchanged here
    or there. A malformed or refused paste is asked for again.
    """
    from infrastructure.analytics.capture import begin_cli_auth_attempt

    reporter.prompt_paste_sign_in(
        _login_url(
            app_url,
            callback_port=None,
            state=state,
            code_challenge=challenge,
            cli_auth_attempt_id=begin_cli_auth_attempt(),
        )
    )
    for attempt in range(1, OPENSRE_ACCOUNT_LOGIN_PASTE_ATTEMPTS + 1):
        pasted = reporter.read_pasted_code().strip()
        if _AUTHORIZATION_CODE_PATTERN.fullmatch(pasted):
            try:
                return _exchange_code(app_url, pasted, verifier)
            except _CodeRejectedError:
                detail = (
                    "That code was not accepted. It may have expired, been used, "
                    "or come from a different sign-in."
                )
        else:
            detail = "That is not an OpenSRE sign-in code. Copy the whole code from the browser."
        if attempt == OPENSRE_ACCOUNT_LOGIN_PASTE_ATTEMPTS:
            raise AccountAuthError(detail)
        reporter.pasted_code_rejected(detail)
    raise AccountAuthError("No sign-in code was pasted.")


def login_account(
    *,
    app_url: str | None = None,
    open_browser: bool = True,
    paste_code: bool = False,
    timeout_seconds: float = 300.0,
    progress: LoginProgress | None = None,
    browser_open: Callable[[str], bool] = webbrowser.open,
) -> AccountLoginResult:
    """Complete webapp authentication and persist the local account safely.

    ``paste_code`` replaces the loopback callback with a code the user pastes,
    for a browser on another machine; it never opens a local browser and
    ``timeout_seconds`` does not apply to it.
    """
    reporter = progress if progress is not None else _SilentLoginProgress()
    if timeout_seconds <= 0:
        raise AccountAuthError("Login timeout must be greater than zero.")
    resolved_app_url = normalize_app_url(app_url)
    state = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()
    if paste_code:
        exchange = _authorize_by_paste(
            resolved_app_url,
            state=state,
            challenge=challenge,
            verifier=verifier,
            reporter=reporter,
        )
        reporter.authorization_received()
    else:
        code = _authorize_by_loopback(
            resolved_app_url,
            state=state,
            challenge=challenge,
            open_browser=open_browser,
            timeout_seconds=timeout_seconds,
            reporter=reporter,
            browser_open=browser_open,
        )
        reporter.authorization_received()
        exchange = _exchange_code(resolved_app_url, code, verifier)
    previous_record = load_account_record()
    previous_token = stored_account_token()
    record = AccountRecord(
        user_id=exchange.user_id,
        organization_id=exchange.organization_id,
        email=exchange.email,
        app_url=resolved_app_url,
        signed_in_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        token_expires_at=exchange.token_expires_at,
        llm_provider=exchange.llm_provider,
        llm_model=exchange.llm_model,
    )
    try:
        save_account_token(exchange.access_token)
        save_account_record(record)
    except Exception as exc:
        _cleanup_failed_login(
            resolved_app_url,
            exchange.access_token,
            previous_record=previous_record,
            previous_token=previous_token,
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
    effective_token_matches_login = not env_token or secrets.compare_digest(
        env_token,
        exchange.access_token,
    )
    if not effective_token_matches_login:
        warning = (
            f"{OPENSRE_ACCOUNT_TOKEN_ENV} is set in your environment and will keep "
            "overriding the token just saved. Unset it so this login is used."
        )
    return AccountLoginResult(
        record=record,
        warning=warning,
        effective_token_matches_login=effective_token_matches_login,
    )


def logout_account() -> AccountLogoutResult:
    """Revoke the remote token and remove account-managed local credentials."""
    record = load_account_record()
    token = stored_account_token()
    app_url = record.app_url if record else normalize_app_url()
    remote_revoked = not token or _revoke_remote(app_url, token)

    try:
        delete_account_token()
        from integrations.github import disconnect_personal_github

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
