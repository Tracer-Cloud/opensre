from __future__ import annotations

import base64
import binascii
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from http import HTTPStatus
from urllib.parse import quote

import httpx

from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.contracts import RemoteObject
from infrastructure.filestorage.enums import BucketExposure, BuiltInProvider, RemoteSyncField
from infrastructure.filestorage.errors import RemoteSyncUnavailableError
from infrastructure.filestorage.exposure import PublicAccessStatus
from infrastructure.filestorage.providers.registry import SetupExtraField, register_object_store

PROVIDER_NAME = BuiltInProvider.AZURE
CREDENTIAL_HINT = (
    "Azure credentials come from the ambient environment (e.g., Azure CLI or env vars)."
)
EXTRA_FIELDS = (
    SetupExtraField(RemoteSyncField.PROFILE, "Azure Storage Account Name (e.g., opensre)"),
)
_API_VERSION = "2026-04-06"


def _get_account_name(config: RemoteSyncConfig) -> str:
    return config.profile or os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "")


class AzureBlobObjectStore:
    """Reads and writes objects under an Azure Storage Container via the REST API."""

    def __init__(
        self,
        config: RemoteSyncConfig,
        *,
        token: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._config = config
        self._account_name = _get_account_name(config)
        if not self._account_name:
            raise RemoteSyncUnavailableError(
                "Azure storage account name is missing. Set it during setup or via AZURE_STORAGE_ACCOUNT_NAME."
            )
        self._token = token if token is not None else _get_azure_access_token()
        self._client = client if client is not None else httpx.Client(timeout=30.0)

    def describe(self) -> str:
        return f"azure-blob://{self._account_name}/{self._config.bucket}/{self._config.prefix}"

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        full_prefix = (
            self._config.key_for(prefix) if prefix else f"{self._config.prefix.rstrip('/')}/"
        )
        try:
            blobs = self._list_all(full_prefix)
            return [
                RemoteObject(
                    key=self._strip_prefix(name),
                    size=_parse_size(props),
                    last_modified=_parse_last_modified(props),
                    etag=_parse_etag(props),
                )
                for name, props in blobs
            ]
        except (httpx.HTTPError, ET.ParseError, ValueError) as exc:
            raise RemoteSyncUnavailableError(
                f"Cannot list {self.describe()} — {_reason(exc)}"
            ) from exc

    def _list_all(self, full_prefix: str) -> list[tuple[str, ET.Element | None]]:
        """Every ``(name, Properties)`` pair under ``full_prefix``, across pages."""
        url = f"https://{self._account_name}.blob.core.windows.net/{self._config.bucket}"
        headers = self._auth_headers()
        marker: str | None = None
        out: list[tuple[str, ET.Element | None]] = []

        while True:
            params = {"restype": "container", "comp": "list", "prefix": full_prefix}
            if marker:
                params["marker"] = marker

            resp = self._client.get(url, params=params, headers=headers)
            resp.raise_for_status()

            root = ET.fromstring(resp.content)
            blobs_el = root.find("Blobs")
            if blobs_el is not None:
                for blob in blobs_el.findall("Blob"):
                    name = blob.findtext("Name")
                    if name:
                        out.append((name, blob.find("Properties")))

            marker = root.findtext("NextMarker")
            if not marker:
                return out

    def get_object(self, key: str) -> bytes:
        url = f"https://{self._account_name}.blob.core.windows.net/{self._config.bucket}/{self._blob_path(key)}"
        try:
            resp = self._client.get(url, headers=self._auth_headers())
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as exc:
            raise RemoteSyncUnavailableError(f"Cannot read {key} — {_reason(exc)}") from exc

    def put_object(self, key: str, data: bytes) -> None:
        url = f"https://{self._account_name}.blob.core.windows.net/{self._config.bucket}/{self._blob_path(key)}"
        headers = {
            **self._auth_headers(),
            "x-ms-blob-type": "BlockBlob",
            "Content-Length": str(len(data)),
        }
        try:
            resp = self._client.put(url, headers=headers, content=data)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RemoteSyncUnavailableError(f"Cannot write {key} — {_reason(exc)}") from exc

    def _blob_path(self, key: str) -> str:
        """URL path segment(s) for ``key``, percent-encoded but with ``/`` left literal.

        A blob name's ``/`` characters are meaningful virtual-directory
        separators baked into the URL path — encoding them to ``%2F`` would
        target a differently-named blob, unlike GCS's opaque single-segment
        object name (:func:`infrastructure.filestorage.providers.gcs.GCSObjectStore.get_object`).
        """
        return quote(self._config.key_for(key), safe="/")

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "x-ms-version": _API_VERSION}

    def _strip_prefix(self, full_key: str) -> str:
        prefix = f"{self._config.prefix.rstrip('/')}/"
        return full_key[len(prefix) :] if full_key.startswith(prefix) else full_key


def _parse_size(props: ET.Element | None) -> int:
    """Object size as int; a missing or malformed Content-Length is treated as unknown."""
    size_str = props.findtext("Content-Length") if props is not None else None
    try:
        return int(size_str) if size_str else 0
    except ValueError:
        return 0


def _parse_last_modified(props: ET.Element | None) -> datetime:
    """RFC 1123 ``Last-Modified``, or raise if it cannot be trusted.

    A missing or malformed timestamp must never be guessed at: defaulting to
    "now" would make an unparseable remote blob look like the newest version,
    and push could then skip re-uploading a local file that should win. The
    ValueError lands in the caller's boundary as RemoteSyncUnavailableError —
    same contract as ``infrastructure.filestorage.providers.gcs._parse_updated``.
    """
    lm_str = props.findtext("Last-Modified") if props is not None else None
    if not lm_str:
        raise ValueError("Azure blob is missing its Last-Modified header")
    return datetime.strptime(lm_str[:-4], "%a, %d %b %Y %H:%M:%S").replace(tzinfo=UTC)


def _parse_etag(props: ET.Element | None) -> str:
    """Extracts a valid MD5 hex hash, or an empty string if unavailable."""
    if props is None:
        return ""

    md5_b64 = props.findtext("Content-MD5")
    if md5_b64:
        try:
            digest = base64.b64decode(md5_b64, validate=True)
            if len(digest) == 16:
                return digest.hex()
        except (ValueError, binascii.Error):
            # Malformed Base64 or non-MD5 content degrades to an unavailable
            # fingerprint instead of crashing the listing.
            pass

    # Missing or invalid Content-MD5 degrades to an unavailable fingerprint ("").
    # Returning Azure's opaque Etag here would place unchanged blobs on an
    # incompatible fingerprint path, because the sync engine compares remote
    # tags with a local MD5 hex.
    return ""


def _get_azure_access_token() -> str:
    """Resolve a Bearer token via Service Principal env vars or the Azure CLI."""
    tenant_id = os.getenv("AZURE_TENANT_ID")
    client_id = os.getenv("AZURE_CLIENT_ID")
    client_secret = os.getenv("AZURE_CLIENT_SECRET")

    if tenant_id and client_id and client_secret:
        url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "scope": "https://storage.azure.com/.default",
        }
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(url, data=data)
                resp.raise_for_status()
                return str(resp.json()["access_token"])
        except Exception as exc:
            raise RemoteSyncUnavailableError(
                f"Failed to authenticate with Azure Entra ID: {exc}"
            ) from exc

    try:
        result = subprocess.run(
            ["az", "account", "get-access-token", "--resource", "https://storage.azure.com/"],
            capture_output=True,
            check=True,
            text=True,
        )
        return str(json.loads(result.stdout)["accessToken"])
    except Exception as exc:
        raise RemoteSyncUnavailableError(
            "Could not resolve Azure credentials. Set AZURE_TENANT_ID, AZURE_CLIENT_ID, "
            "and AZURE_CLIENT_SECRET, or log in with the Azure CLI (`az login`)."
        ) from exc


def _reason(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return f"{type(exc).__name__}: {exc}"


def _factory(config: RemoteSyncConfig) -> AzureBlobObjectStore:
    return AzureBlobObjectStore(config)


def check_public_access(
    config: RemoteSyncConfig, *, client: httpx.Client | None = None
) -> PublicAccessStatus:
    account_name = _get_account_name(config)
    if not account_name:
        return PublicAccessStatus(BucketExposure.UNKNOWN, "AZURE_STORAGE_ACCOUNT_NAME is missing")

    try:
        token = _get_azure_access_token()
    except Exception as exc:
        return PublicAccessStatus(BucketExposure.UNKNOWN, f"cannot check ({type(exc).__name__})")

    url = f"https://{account_name}.blob.core.windows.net/{config.bucket}"
    owns_client = client is None
    cl = client if client is not None else httpx.Client(timeout=30.0)

    try:
        response = cl.get(
            url,
            params={"restype": "container"},
            headers={"Authorization": f"Bearer {token}", "x-ms-version": _API_VERSION},
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            return PublicAccessStatus(
                BucketExposure.UNKNOWN, "missing permission to read container properties"
            )
        return PublicAccessStatus(BucketExposure.UNKNOWN, f"cannot check ({type(exc).__name__})")
    except httpx.HTTPError as exc:
        return PublicAccessStatus(BucketExposure.UNKNOWN, f"cannot check ({type(exc).__name__})")
    finally:
        if owns_client:
            cl.close()

    access = response.headers.get("x-ms-blob-public-access")
    if access in ("blob", "container"):
        return PublicAccessStatus(BucketExposure.PUBLIC)

    return PublicAccessStatus(BucketExposure.PRIVATE)


# No ``max_parallel_uploads``: this backend stays on the cautious shared
# default. ``httpx.Client`` is safe to share across threads, but ``put_object``
# raises on the first non-2xx with no retry, so a throttled write during a wide
# fan-out would abort the push rather than back off.
register_object_store(
    PROVIDER_NAME,
    _factory,
    credential_hint=CREDENTIAL_HINT,
    extra_fields=EXTRA_FIELDS,
    public_access_checker=check_public_access,
)

__all__ = [
    "CREDENTIAL_HINT",
    "EXTRA_FIELDS",
    "PROVIDER_NAME",
    "AzureBlobObjectStore",
    "check_public_access",
]
