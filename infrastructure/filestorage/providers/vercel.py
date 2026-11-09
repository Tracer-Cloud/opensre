"""Vercel Blob backend for remote sync — one registered :class:`ObjectStore`."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import httpx

from config.constants.filestorage import BLOB_READ_WRITE_TOKEN_ENV
from config.constants.vercel import VERCEL_API_BASE_URL, VERCEL_API_TOKEN_ENV, VERCEL_TEAM_ID_ENV
from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.contracts import RemoteObject
from infrastructure.filestorage.enums import BucketExposure, BuiltInProvider
from infrastructure.filestorage.errors import RemoteSyncUnavailableError
from infrastructure.filestorage.exposure import PublicAccessStatus
from infrastructure.filestorage.providers.registry import register_object_store

if TYPE_CHECKING:
    from collections.abc import Iterator

PROVIDER_NAME = BuiltInProvider.VERCEL
CREDENTIAL_HINT = f"Set {BLOB_READ_WRITE_TOKEN_ENV} in the environment (opensre does not store it)."

# Official control-plane URL used by @vercel/blob (list/put).
_API_BASE = "https://vercel.com/api/blob"
_API_VERSION = "12"
_ACCESS = "private"
_LIST_LIMIT = 1000
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# Store-management API — a *different* host and a *different* token type than
# _API_BASE: it does not accept BLOB_READ_WRITE_TOKEN at all (confirmed live:
# that token shape gets a 403 with "invalidToken": true), only a Vercel
# account/team API token. Reuses VERCEL_API_BASE_URL / VERCEL_API_TOKEN_ENV /
# VERCEL_TEAM_ID_ENV — the same host and credential the (unrelated)
# integrations/vercel/ SRE integration already talks to and asks for — rather
# than inventing a second Vercel token convention; infrastructure/ cannot import
# integrations/vercel/client.py directly (see docs/ARCHITECTURE.md's tier
# table: platform must never import integrations), so this call is
# hand-rolled instead of shared. Used solely by check_public_access — sync
# itself never calls this host.
_ACCOUNT_API_BASE = VERCEL_API_BASE_URL


class VercelBlobObjectStore:
    """Reads and writes private blobs under one store and prefix.

    Auth is ambient ``BLOB_READ_WRITE_TOKEN`` (same env Vercel documents), not
    stored by opensre. ``RemoteSyncConfig.bucket`` is the store name/id used only
    in human-facing ``describe()`` output.
    """

    def __init__(
        self,
        config: RemoteSyncConfig,
        *,
        token: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._config = config
        resolved = (token if token is not None else _token_from_env()).strip()
        if not resolved:
            raise RemoteSyncUnavailableError(
                f"cannot build a Vercel Blob client — set {BLOB_READ_WRITE_TOKEN_ENV}"
            )
        self._token = resolved
        self._store_id = _store_id_from_token(resolved)
        if not self._store_id:
            raise RemoteSyncUnavailableError(
                "cannot build a Vercel Blob client — token has no store id"
            )
        self._client = client if client is not None else httpx.Client(timeout=_TIMEOUT)
        self._owns_client = client is None

    def describe(self) -> str:
        return f"vercel-blob://{self._config.bucket}/{self._config.prefix}"

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        # Trailing slash so prefix "opensre" cannot also match "opensre-backup/".
        full_prefix = (
            self._config.key_for(prefix) if prefix else f"{self._config.prefix.rstrip('/')}/"
        )
        out: list[RemoteObject] = []
        try:
            for blob in self._iter_blobs(full_prefix):
                # _blobs_from_list_payload already guaranteed a non-empty str.
                pathname = blob["pathname"]
                out.append(
                    RemoteObject(
                        key=self._strip_prefix(pathname),
                        size=int(blob.get("size", 0)),
                        last_modified=_parse_uploaded_at(blob.get("uploadedAt")),
                        etag=str(blob.get("etag", "")).strip('"'),
                    )
                )
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            # HTTP failures and malformed success payloads (non-JSON body,
            # non-object root, unparsable field types) all map to unavailable
            # so CLI/REPL get an actionable remote-sync error, not a raw crash.
            raise RemoteSyncUnavailableError(
                f"cannot list {self.describe()} — {_reason(exc)}"
            ) from exc
        return out

    def get_object(self, key: str) -> bytes:
        pathname = self._config.key_for(key)
        url = _private_blob_url(self._store_id, pathname)
        try:
            response = self._client.get(url, headers=self._auth_headers())
            response.raise_for_status()
            return response.content
        except httpx.HTTPError as exc:
            raise RemoteSyncUnavailableError(f"cannot read {key} — {_reason(exc)}") from exc

    def put_object(self, key: str, data: bytes) -> None:
        pathname = self._config.key_for(key)
        params = urlencode({"pathname": pathname})
        headers = {
            **self._auth_headers(),
            "x-vercel-blob-access": _ACCESS,
            "x-allow-overwrite": "true",
            "x-add-random-suffix": "false",
            "x-content-type": "application/octet-stream",
        }
        try:
            response = self._client.put(
                f"{_API_BASE}?{params}",
                content=data,
                headers=headers,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RemoteSyncUnavailableError(f"cannot write {key} — {_reason(exc)}") from exc

    def _iter_blobs(self, prefix: str) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            params: dict[str, str | int] = {
                "prefix": prefix,
                "limit": _LIST_LIMIT,
                "mode": "expanded",
            }
            if cursor:
                params["cursor"] = cursor
            response = self._client.get(
                _API_BASE,
                params=params,
                headers=self._auth_headers(),
            )
            response.raise_for_status()
            payload = _json_object(response, what="list blobs")
            yield from _blobs_from_list_payload(payload)
            if not payload.get("hasMore"):
                break
            next_cursor = payload.get("cursor")
            if not next_cursor:
                break
            cursor = str(next_cursor)

    def _auth_headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._token}",
            "x-api-version": _API_VERSION,
        }

    def _strip_prefix(self, full_key: str) -> str:
        prefix = f"{self._config.prefix.rstrip('/')}/"
        return full_key[len(prefix) :] if full_key.startswith(prefix) else full_key


def _token_from_env() -> str:
    return os.getenv(BLOB_READ_WRITE_TOKEN_ENV, "")


def _store_id_from_token(token: str) -> str:
    """Store id embedded in ``vercel_blob_rw_<storeId>_<secret>`` tokens.

    Matches ``parseStoreIdFromReadWriteToken`` in ``@vercel/blob``.
    """
    parts = token.split("_")
    if len(parts) < 4:
        return ""
    return parts[3]


def _private_blob_url(store_id: str, pathname: str) -> str:
    return f"https://{store_id}.private.blob.vercel-storage.com/{pathname.lstrip('/')}"


def _parse_uploaded_at(value: object) -> datetime:
    """Parse the blob's upload timestamp, or raise if it cannot be trusted.

    A missing or unparseable timestamp must never fall back to "now" — that
    would make incomplete metadata outrank a genuinely newer local file during
    sync, silently discarding the local copy.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        # API returns ISO-8601 with Z suffix.
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    raise ValueError(f"blob 'uploadedAt' is not a usable timestamp: {value!r}")


def _json_object(response: httpx.Response, *, what: str) -> dict[str, Any]:
    """Parse a JSON object body, or raise :class:`ValueError` for the caller."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError(f"malformed JSON while trying to {what}") from exc
    if not isinstance(payload, dict):
        raise ValueError(
            f"expected a JSON object while trying to {what}, got {type(payload).__name__}"
        )
    return payload


def _blobs_from_list_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the ``blobs`` list, or raise if the field shape is unusable.

    An empty list is valid (nothing under the prefix). Missing, null, non-list,
    non-object, or key-less entries must not look like an authoritative empty
    or partial listing — a blob without a real ``pathname`` would otherwise
    become an authoritative object under an empty key, and the sync engine
    would miss the real remote object entirely. ``uploadedAt`` is validated
    separately by :func:`_parse_uploaded_at`, since a missing value there must
    raise rather than default to "now" — same reasoning, different field.
    """
    if "blobs" not in payload:
        raise ValueError("list response is missing the 'blobs' field")
    raw = payload["blobs"]
    if not isinstance(raw, list):
        raise ValueError(f"list response 'blobs' must be a list, got {type(raw).__name__}")
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(
                f"list response blobs[{index}] must be an object, got {type(item).__name__}"
            )
        pathname = item.get("pathname")
        if not isinstance(pathname, str) or not pathname:
            raise ValueError(f"list response blobs[{index}] is missing a 'pathname'")
        out.append(item)
    return out


def _reason(exc: Exception) -> str:
    """The Vercel-side cause, for a local operator to act on."""
    if isinstance(exc, httpx.HTTPStatusError):
        detail = ""
        try:
            body = exc.response.json()
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict) and err.get("message"):
                    detail = f": {err['message']}"
                elif body.get("message"):
                    detail = f": {body['message']}"
        except (ValueError, TypeError):
            detail = ""
        return f"HTTP {exc.response.status_code}{detail}"
    return f"{type(exc).__name__}: {exc}"


def _factory(config: RemoteSyncConfig) -> VercelBlobObjectStore:
    return VercelBlobObjectStore(config)


def check_public_access(
    _config: RemoteSyncConfig, *, client: httpx.Client | None = None
) -> PublicAccessStatus:
    """Ask Vercel whether the configured Blob store's access mode is public.

    ``_config`` is unused: unlike AWS/GCS, the store id here comes from
    ``BLOB_READ_WRITE_TOKEN`` (see below), not ``config.bucket`` — the
    parameter stays only to satisfy the shared ``PublicAccessChecker`` shape
    every provider's checker is called through.

    Unlike S3/GCS, Vercel Blob access is a **store-level, immutable**
    property — set at store creation and the same for every object in it
    (see Vercel's docs: "you cannot change it after the creation of a blob
    store"). ``GET /v1/storage/stores/{id}`` on ``_ACCOUNT_API_BASE`` answers
    it in one call, the same shape as the AWS/GCS checks, but needs two
    ambient values this provider does not otherwise require:

    - ``VERCEL_API_TOKEN_ENV`` (``VERCEL_API_TOKEN``), plus an optional
      ``VERCEL_TEAM_ID_ENV`` (``VERCEL_TEAM_ID``) for a token that spans
      several teams — the same credential the ``integrations/vercel/`` SRE
      integration already asks for, reused here rather than inventing a
      second Vercel token convention. ``BLOB_READ_WRITE_TOKEN`` cannot
      authenticate this endpoint at all (confirmed live: it is rejected with
      ``invalidToken: true``), so this is a genuinely different,
      broader-scoped credential, not an extra permission on the same one.
      Entirely optional: unset, the check degrades to unchecked, same as no
      permission on AWS/GCS.
    - The store id, parsed from ``BLOB_READ_WRITE_TOKEN`` the same way
      :class:`VercelBlobObjectStore` does — ``config.bucket`` is a
      human-facing label, not guaranteed to be the API store id.

    Degrades to :class:`~infrastructure.filestorage.enums.BucketExposure.UNKNOWN`
    rather than raising, and never carries the raw Vercel response body in
    ``detail`` — this result can be echoed straight into a gateway chat
    surface (see ``format_status_lines``), and that text is not vetted for
    that (CWE-209).
    """
    api_token = os.getenv(VERCEL_API_TOKEN_ENV, "").strip()
    if not api_token:
        return PublicAccessStatus(
            BucketExposure.UNKNOWN, f"set {VERCEL_API_TOKEN_ENV} to check this"
        )
    store_id = _store_id_from_token(_token_from_env().strip())
    if not store_id:
        return PublicAccessStatus(
            BucketExposure.UNKNOWN,
            f"cannot determine the store id — set {BLOB_READ_WRITE_TOKEN_ENV}",
        )
    team_id = os.getenv(VERCEL_TEAM_ID_ENV, "").strip()
    owns_client = client is None
    cl = client if client is not None else httpx.Client(timeout=_TIMEOUT)
    try:
        response = cl.get(
            f"{_ACCOUNT_API_BASE}/v1/storage/stores/{store_id}",
            headers={"authorization": f"Bearer {api_token}"},
            params={"teamId": team_id} if team_id else {},
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            return PublicAccessStatus(
                BucketExposure.UNKNOWN, f"{VERCEL_API_TOKEN_ENV} cannot read this store"
            )
        return PublicAccessStatus(BucketExposure.UNKNOWN, f"cannot check ({type(exc).__name__})")
    except (httpx.HTTPError, ValueError) as exc:
        return PublicAccessStatus(BucketExposure.UNKNOWN, f"cannot check ({type(exc).__name__})")
    finally:
        if owns_client:
            cl.close()
    if not isinstance(payload, dict):
        return PublicAccessStatus(BucketExposure.UNKNOWN, "malformed store response")
    store = payload.get("store")
    access = store.get("access") if isinstance(store, dict) else None
    if access == "public":
        return PublicAccessStatus(BucketExposure.PUBLIC)
    if access == "private":
        return PublicAccessStatus(BucketExposure.PRIVATE)
    return PublicAccessStatus(
        BucketExposure.UNKNOWN, "store response did not include an access mode"
    )


# No ``max_parallel_uploads``: this backend stays on the cautious shared
# default. ``httpx.Client`` is safe to share across threads, but ``put_object``
# raises on the first non-2xx with no retry, so a throttled write during a wide
# fan-out would abort the push rather than back off.
register_object_store(
    PROVIDER_NAME,
    _factory,
    credential_hint=CREDENTIAL_HINT,
    public_access_checker=check_public_access,
)

__all__ = ["CREDENTIAL_HINT", "PROVIDER_NAME", "VercelBlobObjectStore", "check_public_access"]
