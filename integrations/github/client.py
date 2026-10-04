"""Small GitHub REST client used by GitHub-backed OpenSRE tools."""

from __future__ import annotations

import http.client
import json
import os
import random
import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any
from urllib import error, parse, request

from config.constants import (
    GH_TOKEN_ENV,
    GITHUB_API_BASE_URL,
    GITHUB_MCP_AUTH_TOKEN_ENV,
    GITHUB_TOKEN_ENV,
)

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
# A GET is retried this many times after a timeout, a dropped connection or a
# gateway error, waiting a jittered backoff that doubles from this base.
_MAX_GET_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 0.5
# A secondary rate limit asking for a longer pause than this is surfaced
# rather than stalling the tool call.
_MAX_RETRY_AFTER_SECONDS = 5
_RETRYABLE_STATUSES = frozenset(
    {HTTPStatus.BAD_GATEWAY, HTTPStatus.SERVICE_UNAVAILABLE, HTTPStatus.GATEWAY_TIMEOUT}
)
_RATE_LIMIT_STATUSES = frozenset({HTTPStatus.FORBIDDEN, HTTPStatus.TOO_MANY_REQUESTS})
# urllib wraps only connect-phase errors in URLError: a timeout or dropped
# connection inside getresponse() or read() escapes raw. ``socket.timeout`` is
# ``TimeoutError``; ``RemoteDisconnected`` and ``IncompleteRead`` are
# ``HTTPException``s.
_TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    TimeoutError,
    ConnectionError,
    http.client.HTTPException,
)


@dataclass
class GitHubApiError(RuntimeError):
    """Typed API failure; exception metadata must remain writable during propagation."""

    message: str
    status_code: int | None = None
    path: str = ""
    method: str = ""
    rate_limit_remaining: str | None = None
    rate_limit_reset: str | None = None

    def __str__(self) -> str:
        if self.status_code is None:
            return self.message
        return f"GitHub API error {self.status_code}: {self.message}"


def resolve_github_token(github_token: str | None = None) -> str:
    """Resolve a GitHub token: explicit → MCP env → GITHUB_TOKEN → GH_TOKEN."""

    return (
        (github_token or "").strip()
        or os.getenv(GITHUB_MCP_AUTH_TOKEN_ENV, "").strip()
        or os.getenv(GITHUB_TOKEN_ENV, "").strip()
        or os.getenv(GH_TOKEN_ENV, "").strip()
    )


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


def _http_error(exc: error.HTTPError, *, path: str, method: str) -> GitHubApiError:
    detail = exc.read().decode("utf-8", errors="replace") if exc.fp is not None else ""
    return GitHubApiError(
        detail or exc.msg or "GitHub API request failed.",
        status_code=exc.code,
        path=path,
        method=method,
        rate_limit_remaining=exc.headers.get("X-RateLimit-Remaining") if exc.headers else None,
        rate_limit_reset=exc.headers.get("X-RateLimit-Reset") if exc.headers else None,
    )


def _transport_error(exc: Exception, *, path: str, method: str, attempts: int) -> GitHubApiError:
    """A timeout or dropped connection as ``GitHubApiError``; names the failure, never the request."""
    if isinstance(exc, error.URLError):
        reason = f"{exc.reason}"
    elif isinstance(exc, TimeoutError):
        reason = "timed out"
    else:
        reason = f"connection failed ({type(exc).__name__})"
    tries = f" after {attempts} attempts" if attempts > 1 else ""
    return GitHubApiError(f"GitHub API request failed: {reason}{tries}", path=path, method=method)


def _backoff_seconds(retry: int) -> float:
    return _RETRY_BACKOFF_SECONDS * 2.0**retry * random.uniform(0.5, 1.5)


def _retry_after_seconds(exc: error.HTTPError) -> float | None:
    """The pause a secondary rate limit asks for, or None when it is not one worth waiting out.

    An exhausted primary limit (``X-RateLimit-Remaining: 0``) lasts until the
    hourly reset, so it surfaces however short ``Retry-After`` claims to be.
    """
    headers = exc.headers
    if exc.code not in _RATE_LIMIT_STATUSES or headers is None:
        return None
    if str(headers.get("X-RateLimit-Remaining") or "").strip() == "0":
        return None
    try:
        seconds = float(str(headers.get("Retry-After") or "").strip())
    except ValueError:
        return None
    return seconds if 0 <= seconds <= _MAX_RETRY_AFTER_SECONDS else None


def _retry_delay(exc: Exception, *, retry: int) -> float | None:
    """Seconds to wait before retrying a GET that raised ``exc``, or None to surface it."""
    if isinstance(exc, error.HTTPError):
        if exc.code in _RETRYABLE_STATUSES:
            return _backoff_seconds(retry)
        wait = _retry_after_seconds(exc)
        return None if wait is None else max(wait, _backoff_seconds(retry))
    if isinstance(exc, error.URLError):
        return _backoff_seconds(retry) if isinstance(exc.reason, _TRANSIENT_ERRORS) else None
    return _backoff_seconds(retry) if isinstance(exc, _TRANSIENT_ERRORS) else None


def _discard_body(exc: error.HTTPError) -> None:
    if exc.fp is not None:
        exc.close()


def _send(req: request.Request, *, path: str) -> tuple[str, Any]:
    """Send ``req`` and read the whole body, returning it with the raw response headers.

    Every failure surfaces as ``GitHubApiError``, including a timeout or a
    dropped connection while the response is read. Only a GET is retried
    (timeouts, dropped connections, 502/503/504, a secondary rate limit
    with a short ``Retry-After``); a write is never sent twice.
    """
    method = req.get_method()
    retries = _MAX_GET_RETRIES if method == "GET" else 0
    attempt = 0
    while True:
        try:
            with request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as response:  # nosemgrep
                return response.read().decode("utf-8"), getattr(response, "headers", None)
        except (OSError, http.client.HTTPException) as exc:
            delay = _retry_delay(exc, retry=attempt) if attempt < retries else None
            if delay is None:
                if isinstance(exc, error.HTTPError):
                    raise _http_error(exc, path=path, method=method) from exc
                raise _transport_error(exc, path=path, method=method, attempts=attempt + 1) from exc
            if isinstance(exc, error.HTTPError):
                _discard_body(exc)
            time.sleep(delay)
        attempt += 1


def _decode_json_payload(raw: str, *, path: str) -> JsonPayload:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GitHubApiError("GitHub API returned invalid JSON.", path=path) from exc
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
    ) -> None:
        self._token = resolve_github_token(github_token)
        self._base_url = base_url.rstrip("/")
        self._allow_unauthenticated_read = allow_unauthenticated_read

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
                "GitHub token is required. Configure github_token, GITHUB_TOKEN, or GH_TOKEN."
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
        raw, headers = _send(req, path=path)
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
                "GitHub token is required. Configure github_token, GITHUB_TOKEN, or GH_TOKEN."
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
            raw, headers = _send(req, path=path)
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
    "GitHubApiError",
    "GitHubRestClient",
    "JsonPayload",
    "next_page_url",
    "resolve_github_token",
]
