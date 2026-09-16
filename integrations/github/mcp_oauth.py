"""GitHub OAuth device-flow authorization for the GitHub MCP integration.

Browser-based "authorize the app" login that yields a GitHub user access token
usable as the GitHub MCP ``Authorization: Bearer`` credential.

Device flow needs only a *public* OAuth App ``client_id`` (no client secret),
which is why it is safe to ship in a distributed CLI. Register an OAuth App with
"Enable Device Flow" checked, then expose its client id via
``OPENSRE_GITHUB_OAUTH_CLIENT_ID`` (or bake it into ``DEFAULT_GITHUB_OAUTH_CLIENT_ID``).

GitHub does not advertise dynamic client registration, so a pre-registered app is
required regardless of the flow; device flow keeps the implementation minimal
(no localhost redirect server, no PKCE plumbing, no embedded secret).
"""

from __future__ import annotations

import logging
import os
import time
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_DEVICE_VERIFICATION_URL = "https://github.com/login/device"
_DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"

# Read-oriented repository investigation scopes. All are listed in the GitHub MCP
# server's advertised ``scopes_supported``.
DEFAULT_GITHUB_OAUTH_SCOPES: tuple[str, ...] = ("repo", "read:org", "read:user")

# Public OAuth App client id shipped with OpenSRE (device flow enabled). This is
# NOT a secret — device flow has no client secret. Override at runtime with
# ``OPENSRE_GITHUB_OAUTH_CLIENT_ID`` to point at a different OAuth App.
DEFAULT_GITHUB_OAUTH_CLIENT_ID = "Ov23li3MyquuARMTSibo"

_MIN_POLL_INTERVAL = 1.0
_SLOW_DOWN_BACKOFF = 5.0


class GitHubDeviceFlowError(RuntimeError):
    """Raised when device-flow authorization cannot complete."""


@dataclass(frozen=True)
class GitHubDeviceCode:
    """Device/user codes returned by the device-authorization request."""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class GitHubDeviceToken:
    """A GitHub user access token obtained via device flow."""

    access_token: str
    token_type: str = "bearer"
    scope: str = ""
    refresh_token: str = ""
    expires_in: int | None = None


def resolve_github_oauth_client_id(explicit: str = "") -> str:
    """Resolve the OAuth App client id from arg, env, then shipped default."""
    return (
        (explicit or "").strip()
        or os.getenv("OPENSRE_GITHUB_OAUTH_CLIENT_ID", "").strip()
        or DEFAULT_GITHUB_OAUTH_CLIENT_ID.strip()
    )


def _device_error_message(payload: dict[str, object]) -> str:
    error = str(payload.get("error") or "").strip()
    if error == "device_flow_disabled":
        return (
            "Device flow is not enabled on this GitHub OAuth App. Open the app's "
            "settings and check 'Enable Device Flow'."
        )
    description = str(payload.get("error_description") or error or "unknown error").strip()
    return f"GitHub device authorization failed: {description}"


def request_github_device_code(
    *,
    client_id: str,
    scopes: Sequence[str] = DEFAULT_GITHUB_OAUTH_SCOPES,
    timeout: float = 15.0,
) -> GitHubDeviceCode:
    """Start device authorization: ask GitHub for a device + user code."""
    response = httpx.post(
        GITHUB_DEVICE_CODE_URL,
        data={"client_id": client_id, "scope": " ".join(scopes)},
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or "device_code" not in payload:
        raise GitHubDeviceFlowError(
            _device_error_message(payload if isinstance(payload, dict) else {})
        )
    return GitHubDeviceCode(
        device_code=str(payload["device_code"]),
        user_code=str(payload.get("user_code", "")),
        verification_uri=str(payload.get("verification_uri") or GITHUB_DEVICE_VERIFICATION_URL),
        expires_in=int(payload.get("expires_in", 900)),
        interval=int(payload.get("interval", 5)),
    )


def poll_github_device_token(
    *,
    client_id: str,
    device_code: GitHubDeviceCode,
    timeout: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> GitHubDeviceToken:
    """Poll the token endpoint until the user approves (or the code expires)."""
    interval = max(float(device_code.interval), _MIN_POLL_INTERVAL)
    deadline = monotonic() + float(device_code.expires_in)
    while True:
        if monotonic() >= deadline:
            raise GitHubDeviceFlowError(
                "Device authorization expired before it was approved. Re-run setup."
            )
        sleep(interval)
        response = httpx.post(
            GITHUB_ACCESS_TOKEN_URL,
            data={
                "client_id": client_id,
                "device_code": device_code.device_code,
                "grant_type": _DEVICE_GRANT_TYPE,
            },
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise GitHubDeviceFlowError("GitHub returned an unexpected token response.")

        error = str(payload.get("error") or "").strip()
        if not error:
            access_token = str(payload.get("access_token") or "")
            if not access_token:
                raise GitHubDeviceFlowError("GitHub returned no access token.")
            expires_raw = payload.get("expires_in")
            return GitHubDeviceToken(
                access_token=access_token,
                token_type=str(payload.get("token_type") or "bearer"),
                scope=str(payload.get("scope") or ""),
                refresh_token=str(payload.get("refresh_token") or ""),
                expires_in=int(expires_raw) if expires_raw is not None else None,
            )
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval = max(interval, float(payload.get("interval", interval + _SLOW_DOWN_BACKOFF)))
            continue
        if error in {"expired_token", "access_denied"}:
            msg = (
                "Authorization was denied in the browser."
                if error == "access_denied"
                else "Device code expired before approval. Re-run setup."
            )
            raise GitHubDeviceFlowError(msg)
        raise GitHubDeviceFlowError(_device_error_message(payload))


def authorize_github_via_device_flow(
    *,
    client_id: str = "",
    scopes: Sequence[str] = DEFAULT_GITHUB_OAUTH_SCOPES,
    open_browser: bool = True,
    on_prompt: Callable[[GitHubDeviceCode], None] | None = None,
    poll_sleep: Callable[[float], None] | None = None,
) -> GitHubDeviceToken:
    """Run the full browser device flow and return a user access token.

    ``on_prompt`` is invoked with the device/user codes so the caller can show
    the user code and verification URL before (optionally) opening the browser.
    """
    resolved_client_id = resolve_github_oauth_client_id(client_id)
    if not resolved_client_id:
        raise GitHubDeviceFlowError(
            "No GitHub OAuth client id is configured. Register an OAuth App with "
            "Device Flow enabled and set OPENSRE_GITHUB_OAUTH_CLIENT_ID."
        )

    device_code = request_github_device_code(client_id=resolved_client_id, scopes=scopes)
    if on_prompt is not None:
        on_prompt(device_code)
    if open_browser:
        opened = False
        try:
            opened = bool(webbrowser.open(device_code.verification_uri))
        except Exception:  # pragma: no cover - headless/no-browser environments
            logger.debug("Could not open a browser for GitHub device authorization", exc_info=True)
        finally:
            from infrastructure.analytics.capture import capture_browser_open_requested

            capture_browser_open_requested(target="github_oauth", opened=opened)
    return poll_github_device_token(
        client_id=resolved_client_id,
        device_code=device_code,
        sleep=poll_sleep or time.sleep,
    )
