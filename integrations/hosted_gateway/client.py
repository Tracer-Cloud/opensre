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

import httpx

from config.account import is_secure_account_origin, load_account_record, resolve_account_token
from config.constants.gateway import PROMPT_PROGRESS_KIND_NOTE, PROMPT_PROGRESS_KINDS
from config.constants.hosted_gateway import (
    HOSTED_GATEWAY_CONNECT_TIMEOUT_SECONDS,
    HOSTED_GATEWAY_HEALTH_PATH,
    HOSTED_GATEWAY_HTTP_TIMEOUT_SECONDS,
    HOSTED_GATEWAY_PROMPTS_PATH,
    HOSTED_GATEWAY_START_PATH,
    HOSTED_GATEWAY_STOP_PATH,
)
from infrastructure.analytics.capture import capture_hosted_gateway_task_submitted

ERR_NOT_SIGNED_IN = "not_signed_in"
ERR_INSECURE_APP_URL = "insecure_app_url"
ERR_UNREACHABLE = "unreachable"
# The app answered, but the gateway behind it did not: starting, restarting, or down.
ERR_GATEWAY_UNAVAILABLE = "gateway_unavailable"
ERR_UNAUTHORIZED = "unauthorized"
ERR_INVALID_RESPONSE = "invalid_response"
# The app is older than this CLI and has no hosted-gateway routes yet.
ERR_NOT_SUPPORTED = "not_supported"
# The organization has no gateway to start or stop.
ERR_NOT_PROVISIONED = "not_provisioned"
# The gateway exists but no task of it is running, so it cannot take a prompt.
ERR_NOT_RUNNING = "not_running"
# The prompt id names nothing the gateway still holds.
ERR_UNKNOWN_PROMPT = "unknown_prompt"
ERR_PROMPT_TOO_LARGE = "prompt_too_large"
#: The prompt is not waiting for an answer, or already took one.
ERR_NOT_WAITING = "not_waiting"
ERR_ALREADY_ANSWERED = "already_answered"

#: A prompt id as the gateway mints it; anything else never becomes part of a URL.
_PROMPT_ID = re.compile(r"^p_[0-9a-f]{32}$")

#: An upstream reason the app may name. Free text never qualifies, so a body cannot
#: leak into what the user is told.
_CAUSE_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

#: Failures before a connection existed, so no byte of the request reached the app.
_CONNECT_FAILURES = (httpx.ConnectError, httpx.ConnectTimeout)

#: The app's answer when it, or the control plane behind it, could not reach the gateway.
_UNAVAILABLE_STATUSES = frozenset(
    {HTTPStatus.BAD_GATEWAY, HTTPStatus.SERVICE_UNAVAILABLE, HTTPStatus.GATEWAY_TIMEOUT}
)

#: Failures that pass on their own: nobody answered, and a later request may succeed.
TRANSIENT_ERRORS = frozenset({ERR_UNREACHABLE, ERR_GATEWAY_UNAVAILABLE})

#: Failures of the account or its setup, not of the service: nothing to report as an incident.
EXPECTED_ERRORS = frozenset(
    {
        ERR_NOT_SIGNED_IN,
        ERR_INSECURE_APP_URL,
        ERR_UNAUTHORIZED,
        ERR_NOT_SUPPORTED,
        ERR_NOT_PROVISIONED,
        ERR_NOT_RUNNING,
        ERR_UNKNOWN_PROMPT,
        ERR_PROMPT_TOO_LARGE,
        ERR_NOT_WAITING,
        ERR_ALREADY_ANSWERED,
    }
)


class HostedGatewayError(RuntimeError):
    """The OpenSRE app refused or could not serve a hosted-gateway request.

    ``code`` is the failure class (from the HTTP status). ``cause_code`` is the
    app's more specific reason when it named one. Neither is a response body
    or the account token.
    """

    def __init__(self, code: str, status: int | None = None, *, cause_code: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.cause_code = cause_code


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
class PromptQuestion:
    """One question the gateway stopped on, with the options it offered."""

    title: str
    options: tuple[str, ...]
    multi_select: bool = False


@dataclass(frozen=True)
class PromptChoice:
    """The structured question behind a ``needs_input`` record, for a real menu."""

    title: str
    questions: tuple[PromptQuestion, ...]
    custom_answer: bool = True
    #: What is being decided: an approval's reason and redacted arguments, for example.
    note: str = ""


@dataclass(frozen=True)
class PromptProgress:
    """One progress line the gateway reported while working on a prompt."""

    index: int
    text: str
    #: ``tool``, ``plan``, ``plan_done``, or ``note``. Older records omit it.
    kind: str = "note"


@dataclass(frozen=True)
class PromptRecord:
    """One prompt on the organization's gateway, as the app reports it."""

    prompt_id: str
    state: str
    answer: str = ""
    question: str = ""
    error: str = ""
    #: Integrations whose tools failed on the gateway during this prompt, by vendor name.
    failed_integrations: tuple[str, ...] = ()
    choice: PromptChoice | None = None
    #: The newest progress lines; ``index`` grows over the prompt's life, so a poller
    #: prints each line once.
    progress: tuple[PromptProgress, ...] = ()
    #: For a follow-up carrying an answer: the prompt whose question it answered.
    parent_prompt_id: str = ""

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
        self._token = token
        self.app_url = app_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.app_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(
                HOSTED_GATEWAY_HTTP_TIMEOUT_SECONDS, connect=HOSTED_GATEWAY_CONNECT_TIMEOUT_SECONDS
            ),
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
        """Ask the app to start the organization's gateway."""
        return _gateway_health(
            self._request("POST", HOSTED_GATEWAY_START_PATH, _LIFECYCLE_REFUSALS)
        )

    def stop(self) -> GatewayHealth:
        """Ask the app to stop the organization's gateway; its state and credentials are kept."""
        return _gateway_health(self._request("POST", HOSTED_GATEWAY_STOP_PATH, _LIFECYCLE_REFUSALS))

    def send_prompt(self, prompt: str, *, context: dict[str, str]) -> PromptRecord:
        """Queue a prompt on the organization's running gateway."""
        from infrastructure.harness_providers.integration_selection import (
            current_github_connection_id,
        )

        connection_id = current_github_connection_id()
        context = dict(context)
        if connection_id and "github_connection_id" not in context:
            context["github_connection_id"] = connection_id
        payload = self._request(
            "POST",
            HOSTED_GATEWAY_PROMPTS_PATH,
            _PROMPT_REFUSALS,
            body={"prompt": prompt, "context": context},
        )
        record = _prompt_record(payload)
        capture_hosted_gateway_task_submitted(record.prompt_id)
        return record

    def answer_prompt(self, prompt_id: str, answer: str) -> PromptRecord:
        """Answer a prompt that stopped to ask; the follow-up prompt's record comes back."""
        if not _PROMPT_ID.fullmatch(prompt_id):
            raise HostedGatewayError(ERR_UNKNOWN_PROMPT)
        payload = self._request(
            "POST",
            f"{HOSTED_GATEWAY_PROMPTS_PATH}/{prompt_id}/answer",
            _PROMPT_ANSWER_REFUSALS,
            body={"answer": answer},
            body_codes=_ANSWER_BODY_CODES,
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
        body_codes: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        try:
            response = self._send(method, path, body)
        except httpx.HTTPError as exc:
            raise HostedGatewayError(ERR_UNREACHABLE) from exc
        refusal = refusals.get(response.status_code)
        if refusal is not None:
            raise self._refused(_refusal_code(response, refusal, body_codes), response)
        if response.status_code in _UNAVAILABLE_STATUSES:
            raise self._refused(ERR_GATEWAY_UNAVAILABLE, response)
        if not response.is_success:
            raise self._refused(f"http_{response.status_code}", response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise HostedGatewayError(ERR_INVALID_RESPONSE, response.status_code) from exc
        if not isinstance(payload, dict):
            raise HostedGatewayError(ERR_INVALID_RESPONSE, response.status_code)
        return payload

    def _refused(self, code: str, response: httpx.Response) -> HostedGatewayError:
        """The error for a non-success response, with the app's cause when it named one."""
        return HostedGatewayError(
            code, response.status_code, cause_code=self._cause(response, code)
        )

    def _cause(self, response: httpx.Response, code: str) -> str:
        cause = _cause_code(response)
        if not cause or cause == code or self._token in cause:
            return ""
        return cause

    def _send(self, method: str, path: str, body: dict[str, Any] | None) -> httpx.Response:
        """Send, with one fresh connection if the first could not be made.

        Only a connect failure is retried: the request never left, so a prompt
        cannot be queued twice. A read timeout may follow an accepted prompt.
        """
        try:
            return self._http.request(method, path, json=body)
        except _CONNECT_FAILURES:
            return self._http.request(method, path, json=body)


#: Status codes every hosted-gateway route uses to refuse a request, as stable client codes.
_REFUSALS: dict[int, str] = {
    HTTPStatus.UNAUTHORIZED: ERR_UNAUTHORIZED,
    HTTPStatus.NOT_FOUND: ERR_NOT_SUPPORTED,
}

#: Start and stop also refuse organizations without a gateway.
_LIFECYCLE_REFUSALS: dict[int, str] = {
    **_REFUSALS,
    HTTPStatus.CONFLICT: ERR_NOT_PROVISIONED,
}

#: A prompt needs a running task; the app answers 409 when there is none.
_PROMPT_REFUSALS: dict[int, str] = {
    **_REFUSALS,
    HTTPStatus.CONFLICT: ERR_NOT_RUNNING,
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: ERR_PROMPT_TOO_LARGE,
}

#: Reading a result: 404 is the prompt, not the route, being unknown.
_PROMPT_RESULT_REFUSALS: dict[int, str] = {
    HTTPStatus.UNAUTHORIZED: ERR_UNAUTHORIZED,
    HTTPStatus.NOT_FOUND: ERR_UNKNOWN_PROMPT,
    HTTPStatus.CONFLICT: ERR_NOT_RUNNING,
}

#: Answering: a 409 is the gateway not running, or the prompt not waiting; the body says which.
_PROMPT_ANSWER_REFUSALS: dict[int, str] = {
    **_PROMPT_RESULT_REFUSALS,
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: ERR_PROMPT_TOO_LARGE,
}
_ANSWER_BODY_CODES = frozenset({ERR_NOT_RUNNING, ERR_NOT_WAITING, ERR_ALREADY_ANSWERED})


def _cause_code(response: httpx.Response) -> str:
    """The body's ``error`` when it is a stable code, else ""."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    code = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(code, str) and _CAUSE_CODE.fullmatch(code):
        return code
    return ""


def _refusal_code(response: httpx.Response, default: str, body_codes: frozenset[str]) -> str:
    """The app's own error code when it is one the caller distinguishes, else ``default``."""
    if not body_codes:
        return default
    try:
        payload = response.json()
    except ValueError:
        return default
    code = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(code, str) and code in body_codes:
        return code
    return default


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
        failed_integrations=_names(payload.get("failed_integrations")),
        choice=_choice(payload.get("choice")),
        progress=_progress(payload.get("progress")),
        parent_prompt_id=_text(payload.get("parent_prompt_id")),
    )


def _progress_kind(value: object) -> str:
    if isinstance(value, str) and value in PROMPT_PROGRESS_KINDS:
        return value
    return PROMPT_PROGRESS_KIND_NOTE


def _progress(value: object) -> tuple[PromptProgress, ...]:
    if not isinstance(value, list):
        return ()
    lines: list[PromptProgress] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        index, text = item.get("index"), item.get("text")
        if isinstance(index, int) and not isinstance(index, bool) and isinstance(text, str):
            lines.append(
                PromptProgress(index=index, text=text, kind=_progress_kind(item.get("kind")))
            )
    return tuple(lines)


def _choice(value: object) -> PromptChoice | None:
    if not isinstance(value, dict):
        return None
    title = value.get("title")
    raw_questions = value.get("questions")
    if not isinstance(title, str) or not isinstance(raw_questions, list):
        return None
    questions: list[PromptQuestion] = []
    for item in raw_questions:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str):
            return None
        options = _names(item.get("options"))
        multi = item.get("multi_select") is True
        questions.append(PromptQuestion(title=item["title"], options=options, multi_select=multi))
    return PromptChoice(
        title=title,
        questions=tuple(questions),
        custom_answer=value.get("custom_answer") is not False,
        note=_text(value.get("note")),
    )


def _names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


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
    if not is_secure_account_origin(app_url):
        raise HostedGatewayError(ERR_INSECURE_APP_URL)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


__all__ = [
    "ERR_GATEWAY_UNAVAILABLE",
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
    "TRANSIENT_ERRORS",
]
