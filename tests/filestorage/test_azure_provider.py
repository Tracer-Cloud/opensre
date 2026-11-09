from __future__ import annotations

import base64
import hashlib

import httpx
import pytest

from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.errors import RemoteSyncUnavailableError
from infrastructure.filestorage.providers.azure import AzureBlobObjectStore


def _config(**overrides: object) -> RemoteSyncConfig:
    base: dict[str, object] = {
        "bucket": "opensre-remote-sync",
        "provider": "azure",
        "prefix": "opensre",
        "profile": "testaccount",
    }
    base.update(overrides)
    return RemoteSyncConfig(**base)  # type: ignore[arg-type]


class _FakeTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.md5_overrides: dict[str, str | None] = {}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        parts = path.lstrip("/").split("/", 1)
        blob_key = parts[1] if len(parts) > 1 else ""
        query = request.url.query.decode("utf-8")

        if request.method == "GET" and "comp=list" in query:
            prefix = request.url.params.get("prefix", "")
            blobs_xml = []
            for k, v in self.objects.items():
                if k.startswith(prefix):
                    if k in self.md5_overrides:
                        override = self.md5_overrides[k]
                        md5_xml = (
                            f"<Content-MD5>{override}</Content-MD5>" if override is not None else ""
                        )
                    else:
                        md5_b64 = base64.b64encode(hashlib.md5(v).digest()).decode("ascii")
                        md5_xml = f"<Content-MD5>{md5_b64}</Content-MD5>"

                    blobs_xml.append(f"""
                        <Blob>
                            <Name>{k}</Name>
                            <Properties>
                                <Content-Length>{len(v)}</Content-Length>
                                <Last-Modified>Sun, 27 Sep 2009 18:41:57 GMT</Last-Modified>
                                <Etag>"fake-etag"</Etag>
                                {md5_xml}
                            </Properties>
                        </Blob>
                    """)

            xml = f"""<?xml version="1.0" encoding="utf-8"?>
            <EnumerationResults>
                <Blobs>
                    {"".join(blobs_xml)}
                </Blobs>
            </EnumerationResults>"""
            return httpx.Response(200, content=xml.encode("utf-8"), request=request)

        if request.method == "GET":
            if blob_key in self.objects:
                return httpx.Response(200, content=self.objects[blob_key], request=request)
            return httpx.Response(404, request=request)

        if request.method == "PUT":
            self.objects[blob_key] = request.content
            return httpx.Response(201, request=request)

        return httpx.Response(400, request=request)


def test_azure_missing_account_name_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
    cfg = RemoteSyncConfig(bucket="b", provider="azure", prefix="p")

    with pytest.raises(RemoteSyncUnavailableError, match="Azure storage account name is missing"):
        AzureBlobObjectStore(cfg, token="fake")


def test_describe_names_account_bucket_and_prefix() -> None:
    store = AzureBlobObjectStore(
        _config(), token="fake", client=httpx.Client(transport=_FakeTransport())
    )
    assert store.describe() == "azure-blob://testaccount/opensre-remote-sync/opensre"


def test_put_list_get_round_trip() -> None:
    transport = _FakeTransport()
    store = AzureBlobObjectStore(_config(), token="fake", client=httpx.Client(transport=transport))

    payload = b'{"turn": 1}\n'
    store.put_object("sessions/a.jsonl", payload)

    listing = store.list_objects("")
    assert len(listing) == 1
    assert listing[0].key == "sessions/a.jsonl"
    assert listing[0].size == len(payload)
    assert listing[0].etag == hashlib.md5(payload).hexdigest()

    assert store.get_object("sessions/a.jsonl") == payload


def test_list_objects_degrades_to_empty_fingerprint_when_content_md5_is_missing() -> None:
    transport = _FakeTransport()
    store = AzureBlobObjectStore(_config(), token="fake", client=httpx.Client(transport=transport))

    transport.objects["opensre/sessions/old.jsonl"] = b"data"
    transport.md5_overrides["opensre/sessions/old.jsonl"] = None

    listing = store.list_objects("")
    assert len(listing) == 1
    assert listing[0].key == "sessions/old.jsonl"
    assert listing[0].etag == ""


def test_list_objects_degrades_to_empty_fingerprint_when_content_md5_is_malformed() -> None:
    transport = _FakeTransport()
    store = AzureBlobObjectStore(_config(), token="fake", client=httpx.Client(transport=transport))

    # Test invalid base64 padding/characters
    transport.objects["opensre/sessions/bad1.jsonl"] = b"data"
    transport.md5_overrides["opensre/sessions/bad1.jsonl"] = "not_valid_b64!!!"

    # Test mathematically valid base64 but with wrong digest length
    transport.objects["opensre/sessions/bad2.jsonl"] = b"data"
    transport.md5_overrides["opensre/sessions/bad2.jsonl"] = base64.b64encode(b"short").decode(
        "ascii"
    )

    listing = store.list_objects("")
    assert len(listing) == 2
    # Both invalid base64 and invalid length fall back to an empty fingerprint ("")
    assert listing[0].etag == ""
    assert listing[1].etag == ""


def test_put_get_round_trip_with_key_needing_percent_encoding() -> None:
    """A blob name with space/# survives the request path; '/' stays literal.

    '#' would otherwise start a URL fragment and a raw space is invalid in a
    URL path, so both must be percent-encoded — but the directory-separating
    '/' must not be, or the request would target a different blob name.
    """
    transport = _FakeTransport()
    store = AzureBlobObjectStore(_config(), token="fake", client=httpx.Client(transport=transport))

    key = "sessions/a b#c.jsonl"
    payload = b"data"
    store.put_object(key, payload)

    assert store.get_object(key) == payload
    listing = store.list_objects("")
    assert listing[0].key == key


def test_azure_sdk_errors_are_translated() -> None:
    class _Failing(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, request=request)

    store = AzureBlobObjectStore(_config(), token="fake", client=httpx.Client(transport=_Failing()))

    with pytest.raises(RemoteSyncUnavailableError) as caught_list:
        store.list_objects("")
    assert "403" in str(caught_list.value)

    with pytest.raises(RemoteSyncUnavailableError) as caught_get:
        store.get_object("test.json")
    assert "403" in str(caught_get.value)

    with pytest.raises(RemoteSyncUnavailableError) as caught_put:
        store.put_object("test.json", b"data")
    assert "403" in str(caught_put.value)
