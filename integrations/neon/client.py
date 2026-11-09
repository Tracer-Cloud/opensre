"""Read-only client for the Neon control-plane API.

Covers the endpoints an incident investigation reads: the project, its
branches, compute endpoints, recent operations, and the Object Storage state,
buckets, and object listings of one branch. Every method is a GET; nothing here
changes a project.
"""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import Any

import httpx

from infrastructure.delivery.notifications.redaction import redact_token
from integrations.neon.config import NeonConfig
from integrations.probes import ProbeResult

_MAX_PATH_SEGMENT_LEN = 128
# Neon project/branch IDs and bucket names are lowercase slugs: letters,
# digits, hyphens (bucket names may also hold dots). Reject anything else so a
# model-supplied value can never add path segments or traversal.
_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ERROR_DETAIL_LIMIT = 300


class NeonAPIError(RuntimeError):
    """A Neon API call that did not return a usable 2xx response."""

    def __init__(
        self, message: str, *, status_code: int | None = None, invalid_input: bool = False
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.invalid_input = invalid_input


def safe_path_segment(raw: str, *, label: str) -> str:
    """Return ``raw`` stripped, or raise when it is not a plain Neon identifier."""
    cleaned = (raw or "").strip()
    if (
        not cleaned
        or len(cleaned) > _MAX_PATH_SEGMENT_LEN
        or ".." in cleaned
        or not _PATH_SEGMENT_RE.fullmatch(cleaned)
    ):
        raise NeonAPIError(
            f"Invalid Neon {label}: {cleaned[:_MAX_PATH_SEGMENT_LEN]!r}.", invalid_input=True
        )
    return cleaned


def _status_message(status_code: int, body: Any) -> str:
    """A short, secret-free reason for a non-2xx Neon response."""
    detail = ""
    if isinstance(body, dict):
        detail = str(body.get("message") or body.get("error") or "").strip()
    if status_code == HTTPStatus.UNAUTHORIZED:
        return "Neon rejected the API key (HTTP 401)."
    if status_code == HTTPStatus.FORBIDDEN:
        reason = f": {detail}" if detail else "."
        return f"The Neon API key cannot access this resource (HTTP 403){reason}"
    if status_code == HTTPStatus.NOT_FOUND:
        reason = f": {detail}" if detail else "."
        return f"Neon resource not found (HTTP 404){reason}"
    if status_code == HTTPStatus.TOO_MANY_REQUESTS:
        return "Neon API rate limit reached (HTTP 429). Retry shortly."
    if status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return f"Neon API is unavailable (HTTP {status_code}). Retry shortly."
    reason = f": {detail}" if detail else "."
    return f"Neon API returned HTTP {status_code}{reason}"


class NeonClient:
    """Synchronous client for the Neon API, scoped to one configured project."""

    def __init__(self, config: NeonConfig, *, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self._transport = transport
        self._client: httpx.Client | None = None

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                headers=self.config.headers,
                timeout=self.config.timeout_seconds,
                transport=self._transport,
            )
        return self._client

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> NeonClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def project_id(self) -> str:
        return safe_path_segment(self.config.project_id, label="project ID")

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = {key: value for key, value in (params or {}).items() if value not in ("", None)}
        try:
            response = self._http().get(path, params=query)
        except httpx.TimeoutException as exc:
            raise NeonAPIError("Neon API request timed out.") from exc
        except httpx.HTTPError as exc:
            reason = redact_token(str(exc), self.config.api_key)[:_ERROR_DETAIL_LIMIT]
            raise NeonAPIError(f"Neon API request failed: {reason}") from exc
        try:
            body: Any = response.json()
        except ValueError:
            body = None
        if not response.is_success:
            raise NeonAPIError(
                redact_token(_status_message(response.status_code, body), self.config.api_key),
                status_code=response.status_code,
            )
        if not isinstance(body, dict):
            raise NeonAPIError("Neon API returned a response that is not a JSON object.")
        return body

    def get_project(self) -> dict[str, Any]:
        """``GET /projects/{project_id}`` — the project record."""
        project = self._get(f"/projects/{self.project_id}").get("project")
        return project if isinstance(project, dict) else {}

    def list_branches(self) -> list[dict[str, Any]]:
        """``GET /projects/{project_id}/branches``."""
        branches = self._get(f"/projects/{self.project_id}/branches").get("branches")
        return [item for item in branches or [] if isinstance(item, dict)]

    def list_endpoints(self) -> list[dict[str, Any]]:
        """``GET /projects/{project_id}/endpoints`` — every compute in the project."""
        endpoints = self._get(f"/projects/{self.project_id}/endpoints").get("endpoints")
        return [item for item in endpoints or [] if isinstance(item, dict)]

    def list_operations(self, limit: int) -> list[dict[str, Any]]:
        """``GET /projects/{project_id}/operations`` — newest first."""
        operations = self._get(
            f"/projects/{self.project_id}/operations", {"limit": max(1, min(limit, 1000))}
        ).get("operations")
        return [item for item in operations or [] if isinstance(item, dict)]

    def resolve_branch_id(self, branch_id: str = "") -> str:
        """The requested branch, else the configured one, else the project default."""
        requested = (branch_id or "").strip() or self.config.branch_id
        if requested:
            return safe_path_segment(requested, label="branch ID")
        for branch in self.list_branches():
            if branch.get("default"):
                return safe_path_segment(str(branch.get("id", "")), label="branch ID")
        raise NeonAPIError("The Neon project has no default branch; pass a branch ID.")

    def get_branch_storage(self, branch_id: str) -> dict[str, Any]:
        """``GET /projects/{project_id}/branches/{branch_id}/storage``."""
        branch = safe_path_segment(branch_id, label="branch ID")
        return self._get(f"/projects/{self.project_id}/branches/{branch}/storage")

    def list_buckets(self, branch_id: str) -> list[dict[str, Any]]:
        """``GET /projects/{project_id}/branches/{branch_id}/buckets``."""
        branch = safe_path_segment(branch_id, label="branch ID")
        buckets = self._get(f"/projects/{self.project_id}/branches/{branch}/buckets").get("buckets")
        return [item for item in buckets or [] if isinstance(item, dict)]

    def list_objects(
        self,
        branch_id: str,
        bucket: str,
        *,
        prefix: str = "",
        delimiter: str = "",
        limit: int = 50,
        cursor: str = "",
    ) -> dict[str, Any]:
        """``GET .../buckets/{bucket}/objects`` — one page of objects and folders."""
        branch = safe_path_segment(branch_id, label="branch ID")
        bucket_name = safe_path_segment(bucket, label="bucket name")
        return self._get(
            f"/projects/{self.project_id}/branches/{branch}/buckets/{bucket_name}/objects",
            {"prefix": prefix, "delimiter": delimiter, "limit": limit, "cursor": cursor},
        )

    def probe_access(self) -> ProbeResult:
        """Validate the key and project ID by reading the project."""
        if not self.config.api_key:
            return ProbeResult.missing("Missing Neon API key.")
        if not self.config.project_id:
            return ProbeResult.missing("Missing Neon project ID.")
        try:
            with self:
                project = self.get_project()
        except NeonAPIError as exc:
            return ProbeResult.failed(str(exc))
        name = str(project.get("name") or self.config.project_id)
        region = str(project.get("region_id") or "unknown region")
        return ProbeResult.passed(
            f"Connected to Neon project {name} ({self.config.project_id}, {region})."
        )


__all__ = ["NeonAPIError", "NeonClient", "safe_path_segment"]
