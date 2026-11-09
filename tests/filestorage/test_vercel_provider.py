"""Unit tests for the built-in Vercel Blob remote-sync backend."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest

from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.engine import content_tag, run_sync
from infrastructure.filestorage.enums import SyncRootName
from infrastructure.filestorage.errors import RemoteSyncUnavailableError
from infrastructure.filestorage.providers.registry import build_object_store, registered_providers
from infrastructure.filestorage.providers.vercel import VercelBlobObjectStore
from infrastructure.filestorage.syncable import SyncRoot

_TOKEN = "vercel_blob_rw_TestStoreId_deadbeefsecret"
_STORE_ID = "TestStoreId"


@pytest.fixture(autouse=True)
def _restore_provider_registry() -> Iterator[None]:
    from infrastructure.filestorage.providers import registry as reg

    with reg._REGISTRY_LOCK:
        snapshot = dict(reg._REGISTRY)
    yield
    with reg._REGISTRY_LOCK:
        reg._REGISTRY.clear()
        reg._REGISTRY.update(snapshot)


def _config(**overrides: object) -> RemoteSyncConfig:
    base: dict[str, object] = {
        "bucket": "opensre-remote-sync",
        "provider": "vercel",
        "prefix": "opensre",
    }
    base.update(overrides)
    return RemoteSyncConfig(**base)  # type: ignore[arg-type]


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


class _FakeTransport(httpx.BaseTransport):
    """In-memory Vercel Blob control plane + private CDN."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploaded_at = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        host = request.url.host

        if host.endswith(".private.blob.vercel-storage.com") and request.method == "GET":
            # Path is /<pathname…>
            pathname = path.lstrip("/")
            body = self.objects.get(pathname)
            if body is None:
                return httpx.Response(404, request=request)
            return httpx.Response(200, content=body, request=request)

        if host in {"vercel.com", "blob.vercel-storage.com"} or path.startswith("/api/blob"):
            return self._control_plane(request)

        return httpx.Response(404, request=request)

    def _control_plane(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            prefix = request.url.params.get("prefix", "")
            blobs = []
            for pathname, data in sorted(self.objects.items()):
                if not pathname.startswith(prefix):
                    continue
                blobs.append(
                    {
                        "url": f"https://{_STORE_ID.lower()}.private.blob.vercel-storage.com/{pathname}",
                        "downloadUrl": (
                            f"https://{_STORE_ID.lower()}.private.blob.vercel-storage.com/"
                            f"{pathname}?download=1"
                        ),
                        "pathname": pathname,
                        "size": len(data),
                        "uploadedAt": self.uploaded_at.isoformat().replace("+00:00", "Z"),
                        "etag": f'"{_md5(data)}"',
                    }
                )
            return httpx.Response(
                200,
                json={"blobs": blobs, "hasMore": False},
                request=request,
            )

        if request.method == "PUT":
            pathname = request.url.params.get("pathname", "")
            data = request.content
            self.objects[pathname] = data
            return httpx.Response(
                200,
                json={
                    "url": f"https://{_STORE_ID.lower()}.private.blob.vercel-storage.com/{pathname}",
                    "pathname": pathname,
                    "etag": f'"{_md5(data)}"',
                    "contentType": "application/octet-stream",
                    "contentDisposition": "attachment",
                    "downloadUrl": (
                        f"https://{_STORE_ID.lower()}.private.blob.vercel-storage.com/"
                        f"{pathname}?download=1"
                    ),
                },
                request=request,
            )

        return httpx.Response(405, request=request)


def _store(transport: _FakeTransport, **config_kw: object) -> VercelBlobObjectStore:
    client = httpx.Client(transport=transport)
    return VercelBlobObjectStore(_config(**config_kw), token=_TOKEN, client=client)


def test_vercel_is_registered_as_built_in() -> None:
    assert "vercel" in registered_providers()
    # Building without a token fails closed with a clear ambient-cred hint.
    with pytest.raises(RemoteSyncUnavailableError, match="BLOB_READ_WRITE_TOKEN"):
        build_object_store(_config())


def test_describe_names_bucket_and_prefix() -> None:
    store = _store(_FakeTransport())
    assert store.describe() == "vercel-blob://opensre-remote-sync/opensre"


def test_put_list_get_round_trip() -> None:
    transport = _FakeTransport()
    store = _store(transport)

    store.put_object("sessions/a.jsonl", b'{"turn": 1}\n')
    listing = store.list_objects("")
    assert len(listing) == 1
    assert listing[0].key == "sessions/a.jsonl"
    assert listing[0].size == len(b'{"turn": 1}\n')
    assert listing[0].etag == _md5(b'{"turn": 1}\n')
    assert store.get_object("sessions/a.jsonl") == b'{"turn": 1}\n'
    # Object lives under the configured prefix inside the store.
    assert "opensre/sessions/a.jsonl" in transport.objects


def test_list_prefix_isolation() -> None:
    transport = _FakeTransport()
    store = _store(transport)
    store.put_object("sessions/a.jsonl", b"a")
    # A sibling prefix must not leak into this store's listing prefix.
    transport.objects["opensre-backup/sessions/b.jsonl"] = b"b"
    keys = {obj.key for obj in store.list_objects("")}
    assert keys == {"sessions/a.jsonl"}


def test_missing_token_names_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    with pytest.raises(RemoteSyncUnavailableError, match="BLOB_READ_WRITE_TOKEN"):
        VercelBlobObjectStore(_config())


def test_http_errors_name_their_cause() -> None:
    class _Failing(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                json={"error": {"code": "forbidden", "message": "Access denied"}},
                request=request,
            )

    store = VercelBlobObjectStore(
        _config(),
        token=_TOKEN,
        client=httpx.Client(transport=_Failing()),
    )
    with pytest.raises(RemoteSyncUnavailableError) as caught:
        store.list_objects("")
    assert "403" in str(caught.value)
    assert "Access denied" in str(caught.value)


def test_malformed_list_payload_is_remote_sync_unavailable() -> None:
    """Success responses that are not a JSON object stay in the provider hierarchy."""

    class _Weird(httpx.BaseTransport):
        def __init__(self, body: bytes) -> None:
            self._body = body

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=self._body,
                headers={"content-type": "application/json"},
                request=request,
            )

    for body in (b"not-json", b'["array"]', b"null"):
        store = VercelBlobObjectStore(
            _config(),
            token=_TOKEN,
            client=httpx.Client(transport=_Weird(body)),
        )
        with pytest.raises(RemoteSyncUnavailableError, match="cannot list"):
            store.list_objects("")


def test_malformed_blobs_field_is_remote_sync_unavailable() -> None:
    """Nested list shape failures must not look like an authoritative empty store."""

    class _Weird(httpx.BaseTransport):
        def __init__(self, payload: object) -> None:
            self._payload = payload

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=self._payload, request=request)

    bad_payloads = (
        {},  # missing blobs
        {"blobs": None},
        {"blobs": "nope"},
        {"blobs": [{"pathname": "ok"}, "not-an-object"]},
    )
    for payload in bad_payloads:
        store = VercelBlobObjectStore(
            _config(),
            token=_TOKEN,
            client=httpx.Client(transport=_Weird(payload)),
        )
        with pytest.raises(RemoteSyncUnavailableError, match="cannot list"):
            store.list_objects("")

    # Empty list is a real empty store — not a failure.
    store = VercelBlobObjectStore(
        _config(),
        token=_TOKEN,
        client=httpx.Client(transport=_Weird({"blobs": [], "hasMore": False})),
    )
    assert store.list_objects("") == []


def test_incomplete_blob_metadata_is_remote_sync_unavailable() -> None:
    """A blob missing pathname/uploadedAt must not become a phantom or "now" entry.

    Defaulting an absent pathname to "" or an absent/unparseable uploadedAt to
    "now" would make incomplete metadata look authoritative: an empty-key
    object masks the real remote key, and a "now" timestamp can make stale
    remote data outrank a genuinely newer local file.
    """

    class _Weird(httpx.BaseTransport):
        def __init__(self, payload: object) -> None:
            self._payload = payload

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=self._payload, request=request)

    bad_blobs = (
        {"uploadedAt": "2024-01-01T00:00:00Z"},  # missing pathname
        {"pathname": "", "uploadedAt": "2024-01-01T00:00:00Z"},  # blank pathname
        {"pathname": "opensre/a.jsonl"},  # missing uploadedAt
        {"pathname": "opensre/a.jsonl", "uploadedAt": None},  # null uploadedAt
        {"pathname": "opensre/a.jsonl", "uploadedAt": "not-a-timestamp"},  # unparseable
        {"pathname": "opensre/a.jsonl", "uploadedAt": 12345},  # wrong type
    )
    for blob in bad_blobs:
        store = VercelBlobObjectStore(
            _config(),
            token=_TOKEN,
            client=httpx.Client(transport=_Weird({"blobs": [blob], "hasMore": False})),
        )
        with pytest.raises(RemoteSyncUnavailableError, match="cannot list"):
            store.list_objects("")


def test_engine_push_restore_via_registry(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Registry → Vercel store → push → pull into a second tree (same contract as S3)."""
    from pathlib import Path

    home = Path(tmp_path_factory.mktemp("vercel-home"))
    sessions = home / "sessions"
    memory = home / "memory"
    sessions.mkdir()
    memory.mkdir()
    (sessions / "a.jsonl").write_bytes(b'{"turn": 1}\n')
    (memory / "fact.md").write_bytes(b"remembered\n")
    roots = (
        SyncRoot(name=SyncRootName.SESSIONS, path=sessions),
        SyncRoot(name=SyncRootName.MEMORY, path=memory),
    )

    transport = _FakeTransport()
    client = httpx.Client(transport=transport)
    store = VercelBlobObjectStore(_config(), token=_TOKEN, client=client)

    report = run_sync(store, roots=roots)
    assert set(report.uploaded) == {"sessions/a.jsonl", "memory/fact.md"}
    assert transport.objects["opensre/sessions/a.jsonl"] == b'{"turn": 1}\n'

    # ETag is content MD5 so a second full sync moves nothing (pull + push each skip both files).
    second = run_sync(store, roots=roots)
    assert second.uploaded == []
    assert second.downloaded == []
    assert second.skipped == 4

    # Pull into a fresh tree.
    other = Path(tmp_path_factory.mktemp("vercel-other"))
    other_sessions = other / "sessions"
    other_memory = other / "memory"
    other_sessions.mkdir()
    other_memory.mkdir()
    other_roots = (
        SyncRoot(name=SyncRootName.SESSIONS, path=other_sessions),
        SyncRoot(name=SyncRootName.MEMORY, path=other_memory),
    )
    pull_report = run_sync(store, roots=other_roots)
    assert set(pull_report.downloaded) == {"sessions/a.jsonl", "memory/fact.md"}
    assert (other_sessions / "a.jsonl").read_text(encoding="utf-8") == '{"turn": 1}\n'
    assert (other_memory / "fact.md").read_text(encoding="utf-8") == "remembered\n"
    # Sanity: content_tag matches Vercel etags for single-part puts.
    assert content_tag(b'{"turn": 1}\n') == _md5(b'{"turn": 1}\n')
