"""Small GitHub REST client used by GitHub-backed OpenSRE tools."""

from __future__ import annotations

import http.client
import json
import random
import ssl
import time
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from typing import Any
from urllib import error, parse, request

from config.constants import (
    GITHUB_API_BASE_URL,
)
from integrations.github.rate_limit import PauseNotice, RateLimitGate, RateLimitPauseTooLong

JsonPayload = dict[str, Any] | list[Any]

# Safety net for paginate(): GitHub's Link-header pagination has no inherent
# upper bound, and some endpoints (e.g. /issues/comments, which returns every
# comment across the whole repository rather than one issue) can run to
# thousands of pages on an active repo, turning one tool call into a
# multi-minute scan. 50 pages at the default 100/page is 5,000 items -- ample
# for any bounded listing (open issues/PRs), while capping runaway endpoints.
_DEFAULT_PAGINATE_MAX_PAGES = 50
# Applies to each socket operation (connect, every read), not the whole
# request. GitHub aborts a REST request after 10 seconds of server time, so a
# socket silent for longer has no answer coming; the 2 extra seconds cover
# connect, TLS and network latency. A 100-run Actions page takes about 2
# seconds to its first byte, so this only cuts off a stalled connection.
_REQUEST_TIMEOUT_SECONDS = 12
# A GET is retried this many times after a timeout, a dropped connection, a
# gateway error or a rate limit it waited out, waiting a jittered backoff that
# doubles from this base.
_MAX_GET_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 0.5
# Rate-limit pauses a client waits out over its lifetime before it fails with
# the time the limit lifts instead. Short, because a caller without a progress
# line would look hung; a caller that shows one passes more.
DEFAULT_RATE_LIMIT_PATIENCE_SECONDS = 5.0
# GitHub's advice for a rate limit that names no pause: wait at least a minute.
_UNNAMED_PAUSE_SECONDS = 60.0
_RETRYABLE_STATUSES = frozenset(
    {HTTPStatus.BAD_GATEWAY, HTTPStatus.SERVICE_UNAVAILABLE, HTTPStatus.GATEWAY_TIMEOUT}
)
_RATE_LIMIT_STATUSES = frozenset({HTTPStatus.FORBIDDEN, HTTPStatus.TOO_MANY_REQUESTS})
# A 403 is a rate limit, not a permission problem, when its body says so.
_SECONDARY_LIMIT_MARKERS = ("secondary rate limit", "abuse detection")
# urllib wraps only connect-phase errors in URLError: a timeout or dropped
# connection inside getresponse() or read() escapes raw. ``socket.timeout`` is
# ``TimeoutError``; ``RemoteDisconnected`` and ``IncompleteRead`` are
# ``HTTPException``s.
_TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    TimeoutError,
    ConnectionError,
    http.client.HTTPException,
)


class GitHubFailureKind(StrEnum):
    """Why a GitHub request failed, which decides whether waiting, a retry or the user fixes it."""

    RATE_LIMITED = "rate_limited"
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"
    TLS_UNTRUSTED = "tls_untrusted"
    UNREACHABLE = "unreachable"
    SERVER_ERROR = "server_error"
    INVALID_RESPONSE = "invalid_response"
    OTHER = "other"


@dataclass
class GitHubApiError(RuntimeError):
    """Typed API failure; exception metadata must remain writable during propagation.

    ``kind`` is set when the client saw more than the status code (a 403 that
    is a rate limit, an untrusted certificate); ``github_failure_kind`` derives
    the rest. ``retry_after_seconds`` is how long a rate limit has left.
    """

    message: str
    status_code: int | None = None
    path: str = ""
    method: str = ""
    rate_limit_remaining: str | None = None
    rate_limit_reset: str | None = None
    kind: GitHubFailureKind | None = None
    retry_after_seconds: float | None = None

    def __str__(self) -> str:
        if self.status_code is None:
            return self.message
        return f"GitHub API error {self.status_code}: {self.message}"


def github_failure_kind(exc: BaseException) -> GitHubFailureKind:
    """The failure class of ``exc``: the client's own reading, else its status code."""
    if isinstance(exc, GitHubApiError):
        if exc.kind is not None:
            return exc.kind
        status = exc.status_code
        if status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
            return GitHubFailureKind.UNAUTHORIZED
        if status == HTTPStatus.NOT_FOUND:
            return GitHubFailureKind.NOT_FOUND
        if status == HTTPStatus.TOO_MANY_REQUESTS:
            return GitHubFailureKind.RATE_LIMITED
        if status is not None and status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            return GitHubFailureKind.SERVER_ERROR
        return GitHubFailureKind.OTHER
    if isinstance(exc, ValueError):
        return GitHubFailureKind.INVALID_RESPONSE
    return GitHubFailureKind.OTHER


def resolve_github_token(github_token: str | None = None) -> str:
    """Return an explicit token without falling back to local or environment credentials."""
    return (github_token or "").strip()


def next_page_url(headers: Any) -> str | None:
    """The ``rel="next"`` target of a response's ``Link`` header, or None on the last page."""
    raw_link = ""
    if hasattr(headers, "get"):
        raw_link = str(headers.get("Link") or headers.get("link") or "")
    for part in raw_link.split(","):
        url_part, _, rel_part = part.partition(";")
        if 'rel="next"' in rel_part or "rel=next" in rel_part:
            return url_part.strip().strip("<>")
    return None


def _header_dict(raw: Any) -> dict[str, str]:
    """Copy response headers. ``X-OAuth-Scopes`` is only present here, not in the body."""
    items = getattr(raw, "items", None)
    if not callable(items):
        return {}
    return {str(key): str(value) for key, value in items()}


def _header(headers: Any, name: str) -> str:
    """One response header as stripped text; empty when absent."""
    if headers is None or not hasattr(headers, "get"):
        return ""
    return str(headers.get(name) or "").strip()


def _header_seconds(headers: Any, name: str) -> float | None:
    try:
        return float(_header(headers, name))
    except ValueError:
        return None


@dataclass(frozen=True)
class _RateLimitPause:
    """The pause a rate-limited response asks for; a secondary limit also wants fewer at once."""

    seconds: float
    secondary: bool


def _rate_limit_pause(exc: error.HTTPError, body: str, *, now: float) -> _RateLimitPause | None:
    """The pause GitHub asks for when ``exc`` is a rate limit, or None for any other failure.

    An exhausted primary limit (``X-RateLimit-Remaining: 0``) lasts until
    ``X-RateLimit-Reset`` (epoch seconds, like ``now``) however short
    ``Retry-After`` claims, and a minute when it names no reset. A secondary
    limit lasts its ``Retry-After``, or a minute when it names none. A 403
    with none of these is a permission problem, not a limit.
    """
    if exc.code not in _RATE_LIMIT_STATUSES:
        return None
    headers = exc.headers
    retry_after = _header_seconds(headers, "Retry-After")
    if _header(headers, "X-RateLimit-Remaining") == "0":
        reset = _header_seconds(headers, "X-RateLimit-Reset")
        until_reset = _UNNAMED_PAUSE_SECONDS if reset is None else reset - now
        return _RateLimitPause(max(retry_after or 0.0, until_reset), secondary=False)
    if retry_after is not None:
        return _RateLimitPause(retry_after, secondary=True)
    text = body.casefold()
    if exc.code == HTTPStatus.TOO_MANY_REQUESTS or any(
        marker in text for marker in _SECONDARY_LIMIT_MARKERS
    ):
        return _RateLimitPause(_UNNAMED_PAUSE_SECONDS, secondary=True)
    return None


def _error_body(exc: error.HTTPError) -> str:
    """Read and close an error response's body; empty when there is none to read."""
    if exc.fp is None:
        return ""
    try:
        return exc.read().decode("utf-8", errors="replace")
    except (OSError, ValueError, http.client.HTTPException):
        # ValueError: the body was already read and closed.
        return ""
    finally:
        exc.close()


def _http_error(
    exc: error.HTTPError, *, body: str, path: str, method: str, retry_after: float | None
) -> GitHubApiError:
    return GitHubApiError(
        body or exc.msg or "GitHub API request failed.",
        status_code=exc.code,
        path=path,
        method=method,
        rate_limit_remaining=exc.headers.get("X-RateLimit-Remaining") if exc.headers else None,
        rate_limit_reset=exc.headers.get("X-RateLimit-Reset") if exc.headers else None,
        kind=GitHubFailureKind.RATE_LIMITED if retry_after is not None else None,
        retry_after_seconds=retry_after,
    )


def _rate_limit_refusal(
    refused: RateLimitPauseTooLong, *, path: str, method: str
) -> GitHubApiError:
    """A request the client did not send because a rate limit outlasts its patience."""
    return GitHubApiError(
        f"GitHub rate limit in force; requests resume in about {refused.seconds:.0f}s.",
        path=path,
        method=method,
        kind=GitHubFailureKind.RATE_LIMITED,
        retry_after_seconds=refused.seconds,
    )


def _transport_error(exc: Exception, *, path: str, method: str, attempts: int) -> GitHubApiError:
    """A timeout, dropped connection or untrusted certificate; names a cause, never the request."""
    if isinstance(exc, error.URLError):
        reason = f"{exc.reason}"
    elif isinstance(exc, TimeoutError):
        reason = "timed out"
    else:
        reason = f"connection failed ({type(exc).__name__})"
    tries = f" after {attempts} attempts" if attempts > 1 else ""
    cause = exc.reason if isinstance(exc, error.URLError) else exc
    kind = (
        GitHubFailureKind.TLS_UNTRUSTED
        if isinstance(cause, ssl.SSLCertVerificationError)
        else GitHubFailureKind.UNREACHABLE
    )
    return GitHubApiError(
        f"GitHub API request failed: {reason}{tries}", path=path, method=method, kind=kind
    )


def _backoff_seconds(retry: int) -> float:
    return _RETRY_BACKOFF_SECONDS * 2.0**retry * random.uniform(0.5, 1.5)


def _is_transient(exc: Exception) -> bool:
    cause = exc.reason if isinstance(exc, error.URLError) else exc
    return isinstance(cause, _TRANSIENT_ERRORS)


def _resend_delay(
    exc: Exception,
    *,
    gate: RateLimitGate,
    path: str,
    method: str,
    retry: int,
    may_resend: bool,
) -> float:
    """Seconds to wait before resending after ``exc``; raises ``GitHubApiError`` when not resent.

    A rate limit pauses ``gate`` instead, for every request of the client, so
    the resend waits there and this returns 0.
    """
    if isinstance(exc, error.HTTPError):
        body = _error_body(exc)
        pause = _rate_limit_pause(exc, body, now=time.time())
        if pause is not None:
            seconds = max(pause.seconds, _backoff_seconds(retry))
            if gate.pause(seconds, secondary=pause.secondary) and may_resend:
                return 0.0
            raise _http_error(
                exc, body=body, path=path, method=method, retry_after=seconds
            ) from exc
        if may_resend and exc.code in _RETRYABLE_STATUSES:
            return _backoff_seconds(retry)
        raise _http_error(exc, body=body, path=path, method=method, retry_after=None) from exc
    if may_resend and _is_transient(exc):
        return _backoff_seconds(retry)
    raise _transport_error(exc, path=path, method=method, attempts=retry + 1) from exc


def _send(req: request.Request, *, path: str, gate: RateLimitGate) -> tuple[str, Any]:
    """Send ``req`` and read the whole body, returning it with the raw response headers.

    Every failure surfaces as ``GitHubApiError``, including a timeout or a
    dropped connection while the response is read. Every send passes
    ``gate``, so a rate limit one request hits holds the client's other
    requests as well. Only a GET is resent (timeouts, dropped connections,
    502/503/504, a rate limit the gate waited out); a write is never sent twice.
    """
    method = req.get_method()
    retries = _MAX_GET_RETRIES if method == "GET" else 0
    attempt = 0
    while True:
        try:
            with (
                gate.turn(),
                request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as response,  # nosemgrep
            ):
                return response.read().decode("utf-8"), getattr(response, "headers", None)
        except RateLimitPauseTooLong as refused:
            raise _rate_limit_refusal(refused, path=path, method=method) from None
        except (OSError, http.client.HTTPException) as exc:
            time.sleep(
                _resend_delay(
                    exc,
                    gate=gate,
                    path=path,
                    method=method,
                    retry=attempt,
                    may_resend=attempt < retries,
                )
            )
        attempt += 1


def _decode_json_payload(raw: str, *, path: str) -> JsonPayload:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GitHubApiError(
            "GitHub API returned invalid JSON.", path=path, kind=GitHubFailureKind.INVALID_RESPONSE
        ) from exc
    if isinstance(parsed, dict | list):
        return parsed
    return {"value": parsed}


class GitHubRestClient:
    """Minimal GitHub REST API client with pagination and typed errors."""

    def __init__(
        self,
        github_token: str | None = None,
        *,
        base_url: str = GITHUB_API_BASE_URL,
        allow_unauthenticated_read: bool = False,
        rate_limit_patience_seconds: float = DEFAULT_RATE_LIMIT_PATIENCE_SECONDS,
        on_rate_limit_pause: PauseNotice | None = None,
    ) -> None:
        """``rate_limit_patience_seconds`` is how long this client waits out rate limits in all.

        ``on_rate_limit_pause`` hears each pause it waits out, in seconds, so a
        caller with a progress line can say why the read went quiet.
        """
        self._token = resolve_github_token(github_token)
        self._base_url = base_url.rstrip("/")
        self._allow_unauthenticated_read = allow_unauthenticated_read
        self._gate = RateLimitGate(
            patience_seconds=rate_limit_patience_seconds, on_pause=on_rate_limit_pause
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        accept: str = "application/vnd.github+json",
        api_version: str = "2022-11-28",
    ) -> JsonPayload:
        payload, _headers = self.request_with_headers(
            method,
            path,
            params=params,
            body=body,
            accept=accept,
            api_version=api_version,
        )
        return payload

    def request_with_headers(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        accept: str = "application/vnd.github+json",
        api_version: str = "2022-11-28",
    ) -> tuple[JsonPayload, dict[str, str]]:
        """One REST call, returning the JSON body and the response headers."""
        if not self._token and not (self._allow_unauthenticated_read and method.upper() == "GET"):
            raise GitHubApiError(
                "An authorized GitHub app connection is required. Connect or reconnect GitHub in the OpenSRE app.",
                kind=GitHubFailureKind.UNAUTHORIZED,
            )

        url = self._url(path, params=params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = request.Request(
            url,
            data=data,
            method=method.upper(),
            headers={
                "Accept": accept,
                "Content-Type": "application/json; charset=utf-8",
                "X-GitHub-Api-Version": api_version,
                **({"Authorization": f"Bearer {self._token}"} if self._token else {}),
            },
        )
        raw, headers = _send(req, path=path, gate=self._gate)
        return _decode_json_payload(raw, path=path), _header_dict(headers)

    def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        accept: str = "application/vnd.github+json",
        api_version: str = "2022-11-28",
        max_pages: int = _DEFAULT_PAGINATE_MAX_PAGES,
        collection_key: str = "items",
    ) -> list[dict[str, Any]]:
        """Follow Link-header pagination, stopping after ``max_pages`` pages.

        ``collection_key`` supports endpoints such as check-runs whose list is
        wrapped in an object instead of returned as the top-level payload.
        Silently returns whatever was collected so far once the cap is hit,
        rather than raising -- callers on a bounded listing never reach the
        cap; callers on an unbounded one get a usable partial result instead
        of an effectively hung tool call.
        """
        if not self._token and not self._allow_unauthenticated_read:
            raise GitHubApiError(
                "An authorized GitHub app connection is required. Connect or reconnect GitHub in the OpenSRE app.",
                kind=GitHubFailureKind.UNAUTHORIZED,
            )

        url: str | None = self._url(path, params=params)
        items: list[dict[str, Any]] = []
        pages_fetched = 0
        while url and pages_fetched < max_pages:
            pages_fetched += 1
            req = request.Request(
                url,
                method="GET",
                headers={
                    "Accept": accept,
                    "X-GitHub-Api-Version": api_version,
                    **({"Authorization": f"Bearer {self._token}"} if self._token else {}),
                },
            )
            raw, headers = _send(req, path=path, gate=self._gate)
            parsed = _decode_json_payload(raw, path=path) if raw.strip() else []
            if isinstance(parsed, list):
                items.extend(item for item in parsed if isinstance(item, dict))
            elif isinstance(parsed, dict):
                raw_items = parsed.get(collection_key)
                if isinstance(raw_items, list):
                    items.extend(item for item in raw_items if isinstance(item, dict))
            url = next_page_url(headers)
        return items

    def _url(self, path: str, *, params: dict[str, Any] | None = None) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            base = path
        else:
            base = f"{self._base_url}/{path.lstrip('/')}"
        query = parse.urlencode(params, doseq=True) if params else ""
        if not query:
            return base
        separator = "&" if "?" in base else "?"
        return f"{base}{separator}{query}"


__all__ = [
    "DEFAULT_RATE_LIMIT_PATIENCE_SECONDS",
    "GitHubApiError",
    "GitHubFailureKind",
    "GitHubRestClient",
    "JsonPayload",
    "github_failure_kind",
    "next_page_url",
    "resolve_github_token",
]
