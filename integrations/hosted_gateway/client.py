"""The signed-in account's view of its organization's hosted gateway.

Every call goes to the OpenSRE app with the account token from
``opensre account login``. The app maps the token to the user's organization
and that organization to its Fargate gateway; nothing here names an
organization or a gateway, so a caller can only ever reach its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from http import HTTPStatus
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

import httpx

from config.account import load_account_record, resolve_account_token
from config.constants.hosted_gateway import (
    HOSTED_GATEWAY_HEALTH_PATH,
    HOSTED_GATEWAY_HTTP_TIMEOUT_SECONDS,
    HOSTED_GATEWAY_LOOPBACK_HOSTS,
    HOSTED_GATEWAY_PROMPTS_PATH,
    HOSTED_GATEWAY_START_PATH,
    HOSTED_GATEWAY_STOP_PATH,
)

ERR_NOT_SIGNED_IN = "not_signed_in"
ERR_INSECURE_APP_URL = "insecure_app_url"
ERR_UNREACHABLE = "unreachable"
ERR_UNAUTHORIZED = "unauthorized"
ERR_INVALID_RESPONSE = "invalid_response"
# The app is older than this CLI and has no hosted-gateway routes yet.
ERR_NOT_SUPPORTED = "not_supported"
# Only an organization admin may start or stop the gateway.
ERR_ADMIN_REQUIRED = "admin_required"
# The organization has no gateway to start or stop.
ERR_NOT_PROVISIONED = "not_provisioned"
# The gateway exists but no task of it is running, so it cannot take a prompt.
ERR_NOT_RUNNING = "not_running"
# The prompt id names nothing the gateway still holds.
ERR_UNKNOWN_PROMPT = "unknown_prompt"
ERR_PROMPT_TOO_LARGE = "prompt_too_large"

#: A prompt id as the gateway mints it; anything else never becomes part of a URL.
_PROMPT_ID = re.compile(r"^p_[0-9a-f]{32}$")

#: Failures of the account or its setup, not of the service: nothing to report as an incident.
EXPECTED_ERRORS = frozenset(
    {
        ERR_NOT_SIGNED_IN,
        ERR_INSECURE_APP_URL,
        ERR_UNAUTHORIZED,
        ERR_NOT_SUPPORTED,
        ERR_ADMIN_REQUIRED,
        ERR_NOT_PROVISIONED,
        ERR_NOT_RUNNING,
        ERR_UNKNOWN_PROMPT,
        ERR_PROMPT_TOO_LARGE,
    }
)


class HostedGatewayError(RuntimeError):
    """The OpenSRE app refused or could not serve a hosted-gateway request.

    Carries a stable ``code`` only; never the account token or a response body.
    """

    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class GatewayHealth:
    """Whether the organization's gateway exists and is serving.

    ``healthy`` is the app's reading of the Fargate service: its desired task
    count is met and nothing is pending.
    """

    provisioned: bool
    healthy: bool
    gateway_id: str = ""
    desired_state: str = ""
    actual_state: str = ""
    size_profile: str = ""
    last_error_code: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class PromptRecord:
    """One prompt on the organization's gateway, as the app reports it."""

    prompt_id: str
    state: str
    answer: str = ""
    question: str = ""
    error: str = ""

    @property
    def settled(self) -> bool:
        return self.state in {"done", "needs_input", "failed"}


class HostedGatewayClient:
    """Thin HTTP client; one instance per command or tool call."""

    def __init__(
        self, *, app_url: str, token: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        _require_secure_origin(app_url)
        if not token:
            raise HostedGatewayError(ERR_NOT_SIGNED_IN)
        self.app_url = app_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.app_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=HOSTED_GATEWAY_HTTP_TIMEOUT_SECONDS,
            follow_redirects=False,
            transport=transport,
        )

    @classmethod
    def from_account(cls) -> HostedGatewayClient:
        """Build from the signed-in account, or raise ``not_signed_in``."""
        record = load_account_record()
        token = resolve_account_token()
        if record is None or not token:
            raise HostedGatewayError(ERR_NOT_SIGNED_IN)
        return cls(app_url=record.app_url, token=token)

    def __enter__(self) -> HostedGatewayClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def health(self) -> GatewayHealth:
        """Ask the app whether this account's organization has a gateway and it is serving."""
        return _gateway_health(self._request("GET", HOSTED_GATEWAY_HEALTH_PATH, _REFUSALS))

    def start(self) -> GatewayHealth:
        """Ask the app to start the organization's gateway; organization admins only."""
        return _gateway_health(
            self._request("POST", HOSTED_GATEWAY_START_PATH, _LIFECYCLE_REFUSALS)
        )

    def stop(self) -> GatewayHealth:
        """Ask the app to stop the organization's gateway; its state and credentials are kept."""
        return _gateway_health(self._request("POST", HOSTED_GATEWAY_STOP_PATH, _LIFECYCLE_REFUSALS))

    def send_prompt(self, prompt: str, *, context: dict[str, str]) -> PromptRecord:
        """Queue a prompt on the organization's running gateway; admins only."""
        payload = self._request(
            "POST",
            HOSTED_GATEWAY_PROMPTS_PATH,
            _PROMPT_REFUSALS,
            body={"prompt": prompt, "context": context},
        )
        return _prompt_record(payload)

    def prompt_result(self, prompt_id: str) -> PromptRecord:
        """Read one prompt's state; ``unknown_prompt`` for an id the gateway does not hold."""
        if not _PROMPT_ID.fullmatch(prompt_id):
            raise HostedGatewayError(ERR_UNKNOWN_PROMPT)
        payload = self._request(
            "GET", f"{HOSTED_GATEWAY_PROMPTS_PATH}/{prompt_id}", _PROMPT_RESULT_REFUSALS
        )
        return _prompt_record(payload)

    def _request(
        self,
        method: str,
        path: str,
        refusals: dict[int, str],
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._http.request(method, path, json=body)
        except httpx.HTTPError as exc:
            raise HostedGatewayError(ERR_UNREACHABLE) from exc
        refusal = refusals.get(response.status_code)
        if refusal is not None:
            raise HostedGatewayError(refusal, response.status_code)
        if not response.is_success:
            raise HostedGatewayError(f"http_{response.status_code}", response.status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise HostedGatewayError(ERR_INVALID_RESPONSE, response.status_code) from exc
        if not isinstance(payload, dict):
            raise HostedGatewayError(ERR_INVALID_RESPONSE, response.status_code)
        return payload


#: Status codes every hosted-gateway route uses to refuse a request, as stable client codes.
_REFUSALS: dict[int, str] = {
    HTTPStatus.UNAUTHORIZED: ERR_UNAUTHORIZED,
    HTTPStatus.NOT_FOUND: ERR_NOT_SUPPORTED,
}

#: Start and stop also refuse non-admins and organizations without a gateway.
_LIFECYCLE_REFUSALS: dict[int, str] = {
    **_REFUSALS,
    HTTPStatus.FORBIDDEN: ERR_ADMIN_REQUIRED,
    HTTPStatus.CONFLICT: ERR_NOT_PROVISIONED,
}

#: A prompt needs an admin and a running task; the app answers 409 for both missing cases.
_PROMPT_REFUSALS: dict[int, str] = {
    **_REFUSALS,
    HTTPStatus.FORBIDDEN: ERR_ADMIN_REQUIRED,
    HTTPStatus.CONFLICT: ERR_NOT_RUNNING,
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: ERR_PROMPT_TOO_LARGE,
}

#: Reading a result: 404 is the prompt, not the route, being unknown.
_PROMPT_RESULT_REFUSALS: dict[int, str] = {
    HTTPStatus.UNAUTHORIZED: ERR_UNAUTHORIZED,
    HTTPStatus.FORBIDDEN: ERR_ADMIN_REQUIRED,
    HTTPStatus.NOT_FOUND: ERR_UNKNOWN_PROMPT,
    HTTPStatus.CONFLICT: ERR_NOT_RUNNING,
}


def _prompt_record(payload: dict[str, Any]) -> PromptRecord:
    prompt_id, state = payload.get("prompt_id"), payload.get("state")
    if not isinstance(prompt_id, str) or not isinstance(state, str) or not prompt_id:
        raise HostedGatewayError(ERR_INVALID_RESPONSE)
    return PromptRecord(
        prompt_id=prompt_id,
        state=state,
        answer=_text(payload.get("answer")),
        question=_text(payload.get("question")),
        error=_text(payload.get("error")),
    )


def _gateway_health(payload: dict[str, Any]) -> GatewayHealth:
    provisioned, healthy = payload.get("provisioned"), payload.get("healthy")
    if not isinstance(provisioned, bool) or not isinstance(healthy, bool):
        raise HostedGatewayError(ERR_INVALID_RESPONSE)
    return GatewayHealth(
        provisioned=provisioned,
        healthy=healthy,
        gateway_id=_text(payload.get("gateway_id")),
        desired_state=_text(payload.get("desired_state")),
        actual_state=_text(payload.get("actual_state")),
        size_profile=_text(payload.get("size_profile")),
        last_error_code=_text(payload.get("last_error_code")),
        updated_at=_text(payload.get("updated_at")),
    )


def _require_secure_origin(app_url: str) -> None:
    """The account token travels only over https, or over http to this machine."""
    parsed = urlsplit(app_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host:
        return
    if parsed.scheme == "http" and host in HOSTED_GATEWAY_LOOPBACK_HOSTS:
        return
    raise HostedGatewayError(ERR_INSECURE_APP_URL)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


__all__ = [
    "ERR_ADMIN_REQUIRED",
    "ERR_INSECURE_APP_URL",
    "ERR_INVALID_RESPONSE",
    "ERR_NOT_PROVISIONED",
    "ERR_NOT_RUNNING",
    "ERR_NOT_SIGNED_IN",
    "ERR_NOT_SUPPORTED",
    "ERR_PROMPT_TOO_LARGE",
    "ERR_UNAUTHORIZED",
    "ERR_UNKNOWN_PROMPT",
    "ERR_UNREACHABLE",
    "EXPECTED_ERRORS",
    "GatewayHealth",
    "HostedGatewayClient",
    "HostedGatewayError",
    "PromptRecord",
]
