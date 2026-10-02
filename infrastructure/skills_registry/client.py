"""HTTP client for the OpenSRE app's skills release API.

``GET /api/skills/release`` is public (product skills are open source) and
answers ``304`` to a matching ``If-None-Match``. Publishing, rollback and
history need a staff account token, or a GitHub OIDC token for the git sync.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from typing import Any

import httpx

from config.constants.skills import (
    SKILLS_HTTP_TIMEOUT_SECONDS,
    SKILLS_RELEASE_MAX_BYTES,
    SKILLS_RELEASE_PATH,
    SKILLS_RELEASES_PATH,
    SKILLS_ROLLBACK_PATH,
)
from config.version import get_opensre_version
from core.agent_harness.spi.skill_releases import ReleaseError, SkillsRelease

#: JSON escaping can roughly double text; anything beyond this is not a release.
_MAX_RESPONSE_BYTES = SKILLS_RELEASE_MAX_BYTES * 2 + 65_536
RUNNER_TOKEN_HEADER = "X-OpenSRE-Runner-Token"


class FetchStatus(StrEnum):
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    NO_RELEASE = "no_release"


@dataclass(frozen=True)
class FetchResult:
    status: FetchStatus
    release: SkillsRelease | None = None
    etag: str = ""


class SkillsApiError(RuntimeError):
    """The skills API refused a request or could not be reached."""

    def __init__(self, message: str, *, status: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


@dataclass(frozen=True)
class SkillsAuth:
    """Credentials for write calls: a staff account token or a CI OIDC token."""

    account_token: str = ""
    runner_token: str = ""

    def headers(self) -> dict[str, str]:
        if self.runner_token:
            return {RUNNER_TOKEN_HEADER: self.runner_token}
        if self.account_token:
            return {"Authorization": f"Bearer {self.account_token}"}
        raise SkillsApiError("Publishing skills needs `opensre account login` (OpenSRE staff).")


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": f"opensre/{get_opensre_version()}",
    }
    if extra:
        headers.update(extra)
    return headers


def _read_json(response: httpx.Response) -> Any:
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > _MAX_RESPONSE_BYTES:
            raise SkillsApiError("skills API response is too large")
    try:
        return json.loads(body)
    except ValueError as exc:
        raise SkillsApiError("skills API returned invalid JSON") from exc


def _error_message(status: int, payload: Any) -> str:
    detail = payload.get("error") if isinstance(payload, dict) else None
    return f"skills API answered {status}" + (f": {detail}" if detail else "")


def fetch_release(
    app_url: str, *, etag: str = "", client: httpx.Client | None = None
) -> FetchResult:
    """Fetch the latest release, or learn that ``etag`` is still current."""
    headers = _headers({"If-None-Match": etag} if etag else None)
    owned = client is None
    http = client or httpx.Client(timeout=SKILLS_HTTP_TIMEOUT_SECONDS, follow_redirects=False)
    try:
        with http.stream("GET", f"{app_url}{SKILLS_RELEASE_PATH}", headers=headers) as response:
            if response.status_code == HTTPStatus.NOT_MODIFIED:
                return FetchResult(FetchStatus.UNCHANGED, etag=etag)
            if response.status_code == HTTPStatus.NOT_FOUND:
                return FetchResult(FetchStatus.NO_RELEASE)
            if response.status_code != HTTPStatus.OK:
                raise SkillsApiError(
                    f"skills API answered {response.status_code}", status=response.status_code
                )
            payload = _read_json(response)
            try:
                release = SkillsRelease.from_document(payload)
            except ReleaseError as exc:
                raise SkillsApiError(f"skills API returned a malformed release: {exc}") from exc
            return FetchResult(
                FetchStatus.UPDATED, release=release, etag=response.headers.get("etag", "")
            )
    except httpx.HTTPError as exc:
        raise SkillsApiError(f"could not reach the skills API: {type(exc).__name__}") from exc
    finally:
        if owned:
            http.close()


def _post(
    app_url: str, path: str, body: dict[str, Any], auth: SkillsAuth, client: httpx.Client | None
) -> Any:
    owned = client is None
    http = client or httpx.Client(timeout=30.0, follow_redirects=False)
    try:
        response = http.post(f"{app_url}{path}", json=body, headers=_headers(auth.headers()))
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code not in (HTTPStatus.OK, HTTPStatus.CREATED):
            raise SkillsApiError(
                _error_message(response.status_code, payload),
                status=response.status_code,
                payload=payload,
            )
        return payload
    except httpx.HTTPError as exc:
        raise SkillsApiError(f"could not reach the skills API: {type(exc).__name__}") from exc
    finally:
        if owned:
            http.close()


def _release_from(payload: Any) -> tuple[SkillsRelease, bool]:
    if not isinstance(payload, dict):
        raise SkillsApiError("skills API returned an unexpected response")
    try:
        release = SkillsRelease.from_document(payload.get("release"))
    except ReleaseError as exc:
        raise SkillsApiError(f"skills API returned a malformed release: {exc}") from exc
    return release, bool(payload.get("unchanged"))


def publish_release(
    app_url: str, body: dict[str, Any], auth: SkillsAuth, *, client: httpx.Client | None = None
) -> tuple[SkillsRelease, bool]:
    """Publish file changes on top of ``base_seq``; returns ``(release, unchanged)``."""
    return _release_from(_post(app_url, SKILLS_RELEASE_PATH, body, auth, client))


def rollback_release(
    app_url: str, auth: SkillsAuth, *, to_seq: int | None = None, client: httpx.Client | None = None
) -> SkillsRelease:
    """Republish an earlier release (default: the parent of the latest) as a new one."""
    body: dict[str, Any] = {} if to_seq is None else {"to_seq": to_seq}
    release, _unchanged = _release_from(_post(app_url, SKILLS_ROLLBACK_PATH, body, auth, client))
    return release


def list_releases(
    app_url: str, auth: SkillsAuth, *, limit: int = 20, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    """Return release history, newest first (staff only)."""
    owned = client is None
    http = client or httpx.Client(timeout=SKILLS_HTTP_TIMEOUT_SECONDS, follow_redirects=False)
    try:
        response = http.get(
            f"{app_url}{SKILLS_RELEASES_PATH}",
            params={"limit": limit},
            headers=_headers(auth.headers()),
        )
        payload = response.json() if response.content else None
        if response.status_code != HTTPStatus.OK or not isinstance(payload, list):
            raise SkillsApiError(
                _error_message(response.status_code, payload), status=response.status_code
            )
        return [item for item in payload if isinstance(item, dict)]
    except (httpx.HTTPError, ValueError) as exc:
        raise SkillsApiError(f"could not read skills history: {type(exc).__name__}") from exc
    finally:
        if owned:
            http.close()


__all__ = [
    "FetchResult",
    "FetchStatus",
    "RUNNER_TOKEN_HEADER",
    "SkillsApiError",
    "SkillsAuth",
    "fetch_release",
    "list_releases",
    "publish_release",
    "rollback_release",
]
