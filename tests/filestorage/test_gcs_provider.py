"""GCS object-store provider: JSON-API mapping, etag conversion, error wrapping."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
import requests
from google.auth.exceptions import DefaultCredentialsError, RefreshError

from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.engine import content_tag, push
from infrastructure.filestorage.enums import SyncRootName
from infrastructure.filestorage.errors import RemoteSyncUnavailableError
from infrastructure.filestorage.providers import gcs
from infrastructure.filestorage.providers.gcs import GCSObjectStore, _content_etag
from infrastructure.filestorage.providers.registry import build_object_store, registered_providers
from infrastructure.filestorage.syncable import SyncRoot

_UPDATED = "2026-01-01T00:00:00.000Z"
_UPDATED_DT = datetime(2026, 1, 1, tzinfo=UTC)


def _b64_md5(data: bytes) -> str:
    """How GCS spells the digest the engine compares in hex."""
    return base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()


class _FakeResponse:
    def __init__(
        self, *, status_code: int = 200, payload: Any = None, content: bytes = b""
    ) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.content = content

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error", response=self)


class _FakeSession:
    """Serves the GCS JSON API from a dict, with control over item metadata."""

    def __init__(self, *, items: list[dict[str, Any]] | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self._items = items
        self.list_params: list[dict[str, Any]] = []
        self.download_urls: list[str] = []

    def get(
        self, url: str, *, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> _FakeResponse:
        _ = timeout
        params = params or {}
        if params.get("alt") == "media":
            self.download_urls.append(url)
            name = unquote(url.rsplit("/o/", 1)[1])
            return _FakeResponse(content=self.objects[name])
        self.list_params.append(params)
        if self._items is not None:
            items = self._items
        else:
            prefix = params.get("prefix", "")
            items = [
                {
                    "name": key,
                    "size": str(len(data)),
                    "updated": _UPDATED,
                    "md5Hash": _b64_md5(data),
                }
                for key, data in sorted(self.objects.items())
                if key.startswith(prefix)
            ]
        return _FakeResponse(payload={"items": items})

    def post(
        self,
        _url: str,
        *,
        params: dict[str, Any] | None = None,
        data: bytes | None = None,
        timeout: float | None = None,
    ) -> _FakeResponse:
        _ = timeout
        assert params is not None and data is not None
        self.objects[str(params["name"])] = data
        return _FakeResponse(payload={"name": params["name"]})


class _Failing:
    def get(self, *_args: object, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse(status_code=403)

    def post(self, *_args: object, **_kwargs: object) -> _FakeResponse:
        raise requests.ConnectionError("connection dropped")


@pytest.fixture
def roots(tmp_path: Path) -> tuple[SyncRoot, ...]:
    sessions = tmp_path / "sessions"
    memory = tmp_path / "memory"
    sessions.mkdir()
    memory.mkdir()
    (sessions / "a.jsonl").write_text('{"turn": 1}\n', encoding="utf-8")
    (memory / "fact.md").write_text("remembered\n", encoding="utf-8")
    return (
        SyncRoot(name=SyncRootName.SESSIONS, path=sessions),
        SyncRoot(name=SyncRootName.MEMORY, path=memory),
    )


def test_describe_shows_the_gs_destination() -> None:
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), session=_FakeSession())
    assert store.describe() == "gs://b/opensre"


def test_list_maps_item_fields_strips_prefix_and_converts_md5() -> None:
    data = b'{"turn": 1}\n'
    session = _FakeSession()
    session.objects["opensre/sessions/a.jsonl"] = data
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), session=session)

    (obj,) = store.list_objects("")

    assert obj.key == "sessions/a.jsonl"
    assert obj.size == len(data)
    assert obj.last_modified == _UPDATED_DT
    assert obj.etag == content_tag(data)
    # Bare listing scopes under the configured prefix with a trailing slash, so
    # "opensre" cannot also match "opensre-backup/".
    assert session.list_params[0]["prefix"] == "opensre/"


def test_list_passes_a_relative_prefix_through() -> None:
    session = _FakeSession()
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), session=session)
    store.list_objects("sessions")
    assert session.list_params[0]["prefix"] == "opensre/sessions"


def test_etag_comes_from_md5_hash_never_from_the_resource_etag() -> None:
    data = b"payload\n"
    generation_tag = "COD/+u3o/JUDEAE="
    item = {
        "name": "opensre/sessions/a.jsonl",
        "size": str(len(data)),
        "updated": _UPDATED,
        "md5Hash": _b64_md5(data),
        "etag": generation_tag,
    }
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), session=_FakeSession(items=[item]))

    (obj,) = store.list_objects("")

    assert obj.etag == content_tag(data)
    assert obj.etag != generation_tag


def test_composite_object_without_md5_hash_gets_no_tag() -> None:
    item = {"name": "opensre/sessions/big.jsonl", "size": "100", "updated": _UPDATED}
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), session=_FakeSession(items=[item]))
    (obj,) = store.list_objects("")
    assert obj.etag == ""


def test_malformed_md5_hash_gets_no_tag() -> None:
    assert _content_etag("x") == ""
    # Valid-alphabet padding with a stray character must not silently decode.
    assert _content_etag("AAAAAAAAAAAAAAAAAAAAAA==!") == ""
    # Well-formed base64 of anything but a 16-byte digest is not an MD5 tag.
    assert _content_etag(base64.b64encode(b"short").decode()) == ""


def test_missing_updated_timestamp_fails_closed() -> None:
    """An object without a trustworthy timestamp must stop the sync, not be overwritten."""
    item = {"name": "opensre/sessions/a.jsonl", "size": "3"}
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), session=_FakeSession(items=[item]))
    with pytest.raises(RemoteSyncUnavailableError, match="cannot list"):
        store.list_objects("")


def test_push_never_uploads_over_an_unknown_remote_timestamp(roots: tuple[SyncRoot, ...]) -> None:
    item = {"name": "opensre/sessions/a.jsonl", "size": "3"}  # no updated field
    session = _FakeSession(items=[item])
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), session=session)

    with pytest.raises(RemoteSyncUnavailableError, match="cannot list"):
        push(store, roots=roots)

    # The sync stopped at the listing: nothing local was sent over the wire.
    assert session.objects == {}


def test_get_and_put_route_through_the_configured_prefix() -> None:
    session = _FakeSession()
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), session=session)

    store.put_object("sessions/a.jsonl", b"data")
    assert session.objects["opensre/sessions/a.jsonl"] == b"data"
    assert store.get_object("sessions/a.jsonl") == b"data"


def test_object_names_are_url_quoted_in_the_download_path() -> None:
    session = _FakeSession()
    session.objects["opensre/sessions/a b.jsonl"] = b"x"
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), session=session)

    assert store.get_object("sessions/a b.jsonl") == b"x"
    assert "%20" in session.download_urls[0]


def test_list_paginates_via_next_page_token() -> None:
    class _Paged(_FakeSession):
        def get(
            self, _url: str, *, params: dict[str, Any] | None = None, timeout: float | None = None
        ) -> _FakeResponse:
            _ = timeout
            params = params or {}
            self.list_params.append(params)
            if params.get("pageToken") == "t2":
                return _FakeResponse(
                    payload={
                        "items": [
                            {"name": "opensre/sessions/b.jsonl", "size": "3", "updated": _UPDATED}
                        ]
                    }
                )
            return _FakeResponse(
                payload={
                    "items": [
                        {"name": "opensre/sessions/a.jsonl", "size": "3", "updated": _UPDATED}
                    ],
                    "nextPageToken": "t2",
                }
            )

    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), session=_Paged())
    keys = [obj.key for obj in store.list_objects("")]
    assert keys == ["sessions/a.jsonl", "sessions/b.jsonl"]


def test_gcs_failures_name_their_cause() -> None:
    store = GCSObjectStore(RemoteSyncConfig(bucket="missing"), session=_Failing())
    with pytest.raises(RemoteSyncUnavailableError, match="HTTPError"):
        store.list_objects("")
    with pytest.raises(RemoteSyncUnavailableError, match="HTTPError"):
        store.get_object("k")
    with pytest.raises(RemoteSyncUnavailableError, match="ConnectionError"):
        store.put_object("k", b"d")


def test_credential_refresh_failures_are_wrapped() -> None:
    """An expired ADC token raises RefreshError mid-request, outside RequestException."""

    class _Expiring:
        def get(self, *_args: object, **_kwargs: object) -> _FakeResponse:
            raise RefreshError("token expired")

        def post(self, *_args: object, **_kwargs: object) -> _FakeResponse:
            raise RefreshError("token expired")

    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), session=_Expiring())
    with pytest.raises(RemoteSyncUnavailableError, match="cannot list"):
        store.list_objects("")
    with pytest.raises(RemoteSyncUnavailableError, match="cannot read"):
        store.get_object("k")
    with pytest.raises(RemoteSyncUnavailableError, match="cannot write"):
        store.put_object("k", b"d")


@pytest.mark.parametrize(
    "payload",
    [
        ["not", "an", "object"],
        {"items": "not-a-list"},
        {"items": [{"size": "3"}]},
        {"items": [{"name": 42}]},
    ],
)
def test_malformed_list_payloads_are_wrapped(payload: dict[str, Any] | list[str]) -> None:
    """A 200 response with a malformed body must not crash outside the boundary."""

    class _Raw:
        def get(self, *_args: object, **_kwargs: object) -> _FakeResponse:
            return _FakeResponse(payload=payload)

        def post(self, *_args: object, **_kwargs: object) -> _FakeResponse:
            raise AssertionError("not reached")

    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), session=_Raw())
    with pytest.raises(RemoteSyncUnavailableError, match="cannot list"):
        store.list_objects("")


def test_naive_timestamp_fails_closed() -> None:
    """An offset-less timestamp cannot be ordered against aware UTC mtimes."""
    item = {"name": "opensre/sessions/a.jsonl", "size": "3", "updated": "2026-01-01T00:00:00"}
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), session=_FakeSession(items=[item]))
    with pytest.raises(RemoteSyncUnavailableError, match="cannot list"):
        store.list_objects("")


@pytest.mark.parametrize(
    ("item", "expected_size", "expected_etag"),
    [
        ({"name": "opensre/sessions/a.jsonl", "updated": _UPDATED, "size": None}, 0, ""),
        ({"name": "opensre/sessions/a.jsonl", "updated": _UPDATED, "size": "not-a-number"}, 0, ""),
        ({"name": "opensre/sessions/a.jsonl", "updated": _UPDATED, "md5Hash": 42}, 0, ""),
    ],
)
def test_malformed_metadata_fields_fall_back_instead_of_escaping(
    item: dict[str, Any], expected_size: int, expected_etag: str
) -> None:
    """Garbage size/hash metadata degrades like a missing value; it never escapes."""
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), session=_FakeSession(items=[item]))
    (obj,) = store.list_objects("")
    assert obj.size == expected_size
    assert obj.etag == expected_etag


def test_missing_credentials_fail_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_args: object, **_kwargs: object) -> object:
        raise DefaultCredentialsError("no credentials")

    monkeypatch.setattr(gcs.google.auth, "default", _raise)
    with pytest.raises(RemoteSyncUnavailableError, match="cannot build GCS credentials"):
        GCSObjectStore(RemoteSyncConfig(bucket="b"))


def test_gcs_is_a_registered_built_in_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "gcs" in registered_providers()
    fake = _FakeSession()
    monkeypatch.setattr(gcs, "_build_session", lambda: fake)
    built = build_object_store(RemoteSyncConfig(bucket="b", provider="gcs"))
    assert isinstance(built, GCSObjectStore)


def test_second_push_reuploads_nothing(roots: tuple[SyncRoot, ...]) -> None:
    """The md5 conversion must hold through the real engine, not just the mapping."""
    store = GCSObjectStore(RemoteSyncConfig(bucket="b"), session=_FakeSession())

    first = push(store, roots=roots)
    assert "sessions/a.jsonl" in first.uploaded

    second = push(store, roots=roots)
    assert second.uploaded == []
    assert second.skipped == 2
