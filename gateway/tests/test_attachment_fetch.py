"""Credential and redirect boundaries for shared gateway attachment fetches."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from http import HTTPStatus
from typing import Any

import pytest

from gateway.core.attachments import fetch

_AUTHORIZATION = "Bearer test-credential"
_ALLOWED_HOSTS = ("vendor.example",)
_INITIAL_URL = "https://files.vendor.example/attachment.txt"


class _FakeResponse:
    def __init__(
        self,
        status_code: HTTPStatus,
        *,
        headers: dict[str, str] | None = None,
        chunks: tuple[bytes, ...] = (),
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks

    def iter_bytes(self) -> Iterator[bytes]:
        yield from self._chunks

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _RecordingStream:
    def __init__(self, *responses: _FakeResponse) -> None:
        self._responses = deque(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._responses.popleft()


def _download(*, max_bytes: int = 256, max_redirects: int = 5) -> bytes | None:
    return fetch.download_attachment(
        _INITIAL_URL,
        authorization=_AUTHORIZATION,
        host_suffixes=_ALLOWED_HOSTS,
        max_bytes=max_bytes,
        timeout=10.0,
        log_prefix="[test-attachment]",
        max_redirects=max_redirects,
    )


def test_allowed_host_requires_https_and_a_domain_boundary() -> None:
    assert fetch.is_allowed_host(_INITIAL_URL, _ALLOWED_HOSTS)
    assert fetch.is_allowed_host("https://vendor.example/file", _ALLOWED_HOSTS)
    assert not fetch.is_allowed_host("http://files.vendor.example/file", _ALLOWED_HOSTS)
    assert not fetch.is_allowed_host("https://vendor.example.evil.test/file", _ALLOWED_HOSTS)
    assert not fetch.is_allowed_host("https://evil.test/file", _ALLOWED_HOSTS)


def test_initial_off_allowlist_url_is_not_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _RecordingStream()
    monkeypatch.setattr(fetch.httpx, "stream", stream)

    result = fetch.download_attachment(
        "https://evil.test/attachment.txt",
        authorization=_AUTHORIZATION,
        host_suffixes=_ALLOWED_HOSTS,
        max_bytes=256,
        timeout=10.0,
        log_prefix="[test-attachment]",
    )

    assert result is None
    assert stream.calls == []


def test_off_allowlist_redirect_is_not_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _RecordingStream(
        _FakeResponse(
            HTTPStatus.TEMPORARY_REDIRECT,
            headers={"location": "https://evil.test/steal"},
        )
    )
    monkeypatch.setattr(fetch.httpx, "stream", stream)

    result = _download()

    assert result is None
    assert [call["url"] for call in stream.calls] == [_INITIAL_URL]


def test_allowlisted_redirect_returns_bytes_and_keeps_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redirected_url = "https://cdn.vendor.example/attachment.txt"
    stream = _RecordingStream(
        _FakeResponse(
            HTTPStatus.PERMANENT_REDIRECT,
            headers={"location": redirected_url},
        ),
        _FakeResponse(HTTPStatus.OK, chunks=(b"hello ", b"world")),
    )
    monkeypatch.setattr(fetch.httpx, "stream", stream)

    result = _download()

    assert result == b"hello world"
    assert [call["url"] for call in stream.calls] == [_INITIAL_URL, redirected_url]
    assert all(call["headers"] == {"Authorization": _AUTHORIZATION} for call in stream.calls)
    assert all(call["follow_redirects"] is False for call in stream.calls)


def test_redirect_limit_stops_a_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    redirect = _FakeResponse(
        HTTPStatus.FOUND,
        headers={"location": "/attachment.txt"},
    )
    stream = _RecordingStream(redirect, redirect, redirect)
    monkeypatch.setattr(fetch.httpx, "stream", stream)

    result = _download(max_redirects=2)

    assert result is None
    assert len(stream.calls) == 3


def test_oversized_payload_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _RecordingStream(_FakeResponse(HTTPStatus.OK, chunks=(b"1234", b"5678")))
    monkeypatch.setattr(fetch.httpx, "stream", stream)

    result = _download(max_bytes=7)

    assert result is None
