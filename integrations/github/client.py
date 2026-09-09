"""Small GitHub REST client used by GitHub-backed OpenSRE tools."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
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


@dataclass
class GitHubApiError(RuntimeError):
    """Typed API failure; exception metadata must remain writable during propagation."""

    message: str
    status_code: int | None = None
    path: str = ""
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


def _next_link(headers: Any) -> str | None:
    raw_link = ""
    if hasattr(headers, "get"):
        raw_link = str(headers.get("Link") or headers.get("link") or "")
    for part in raw_link.split(","):
        url_part, _, rel_part = part.partition(";")
        if 'rel="next"' in rel_part or "rel=next" in rel_part:
            return url_part.strip().strip("<>")
    return None


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
        try:
            with request.urlopen(req, timeout=20) as response:  # nosemgrep
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = ""
            if exc.fp is not None:
                detail = exc.read().decode("utf-8", errors="replace")
            message = detail or exc.msg or "GitHub API request failed."
            raise GitHubApiError(
                message,
                status_code=exc.code,
                path=path,
                rate_limit_remaining=exc.headers.get("X-RateLimit-Remaining")
                if exc.headers
                else None,
                rate_limit_reset=exc.headers.get("X-RateLimit-Reset") if exc.headers else None,
            ) from exc
        except error.URLError as exc:
            raise GitHubApiError(f"GitHub API request failed: {exc.reason}", path=path) from exc

        return _decode_json_payload(raw, path=path)

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
            try:
                with request.urlopen(req, timeout=20) as response:  # nosemgrep
                    raw = response.read().decode("utf-8")
                    headers = getattr(response, "headers", {})
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
                raise GitHubApiError(
                    detail or exc.msg or "GitHub API request failed.",
                    status_code=exc.code,
                    path=path,
                    rate_limit_remaining=exc.headers.get("X-RateLimit-Remaining")
                    if exc.headers
                    else None,
                    rate_limit_reset=exc.headers.get("X-RateLimit-Reset") if exc.headers else None,
                ) from exc
            except error.URLError as exc:
                raise GitHubApiError(f"GitHub API request failed: {exc.reason}", path=path) from exc

            parsed = _decode_json_payload(raw, path=path) if raw.strip() else []
            if isinstance(parsed, list):
                items.extend(item for item in parsed if isinstance(item, dict))
            elif isinstance(parsed, dict):
                raw_items = parsed.get(collection_key)
                if isinstance(raw_items, list):
                    items.extend(item for item in raw_items if isinstance(item, dict))
            url = _next_link(headers)
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


__all__ = ["GitHubApiError", "GitHubRestClient", "JsonPayload", "resolve_github_token"]
