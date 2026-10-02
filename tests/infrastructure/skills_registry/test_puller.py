"""The background pull stores only verified releases and revalidates with an ETag."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx
import pytest

from core.agent_harness.prompts.skills.snapshot import release_store
from infrastructure.skills_registry import (
    FetchResult,
    FetchStatus,
    PullStatus,
    fetch_release,
    pull_once,
)
from infrastructure.skills_registry import puller as puller_module
from tests.utils.skill_releases import ReleaseSigner, bundled_files, release_signer

__all__ = ["release_signer"]

_APP = "https://app.example.test"


def _serve(responses: list[httpx.Response], seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses.pop(0)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_release_revalidates_with_the_etag(release_signer: ReleaseSigner) -> None:
    document = release_signer.sign(bundled_files(), seq=5).to_document()
    seen: list[httpx.Request] = []
    client = _serve(
        [
            httpx.Response(HTTPStatus.OK, json=document, headers={"ETag": '"5"'}),
            httpx.Response(HTTPStatus.NOT_MODIFIED),
            httpx.Response(HTTPStatus.NOT_FOUND, text="<html>no route</html>"),
        ],
        seen,
    )

    first = fetch_release(_APP, client=client)
    second = fetch_release(_APP, etag=first.etag, client=client)
    third = fetch_release(_APP, client=client)

    assert first.status is FetchStatus.UPDATED and first.release is not None
    assert first.release.seq == 5
    assert seen[1].headers["If-None-Match"] == '"5"'
    assert second.status is FetchStatus.UNCHANGED
    assert third.status is FetchStatus.NO_RELEASE


def _fake_fetch(results: list[Any], calls: list[str]) -> Any:
    def fetch(app_url: str, *, etag: str = "", client: object = None) -> Any:
        calls.append(etag)
        return results.pop(0)

    return fetch


def test_pull_stores_a_verified_release_and_keeps_its_etag(
    release_signer: ReleaseSigner, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = release_signer.sign(bundled_files(), seq=8)
    calls: list[str] = []
    monkeypatch.setattr(
        puller_module,
        "fetch_release",
        _fake_fetch(
            [
                FetchResult(FetchStatus.UPDATED, release=release, etag='"8"'),
                FetchResult(FetchStatus.UNCHANGED, etag='"8"'),
            ],
            calls,
        ),
    )

    assert pull_once(force=True, app_url=_APP).status is PullStatus.STORED
    assert release_store.latest_stored_seq() == 8
    assert pull_once(force=True, app_url=_APP).status is PullStatus.UNCHANGED
    assert calls == ["", '"8"']
    # Another process just checked: a scheduled pull skips the network.
    assert pull_once(app_url=_APP).status is PullStatus.SKIPPED


def test_pull_rejects_an_unsigned_release_and_does_not_cache_its_etag(
    release_signer: ReleaseSigner, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = ReleaseSigner(release_signer.key, key_id="unknown-key")
    release = other.sign(bundled_files(), seq=3)
    calls: list[str] = []
    monkeypatch.setattr(
        puller_module,
        "fetch_release",
        _fake_fetch(
            [
                FetchResult(FetchStatus.UPDATED, release=release, etag='"3"'),
                FetchResult(FetchStatus.UPDATED, release=release, etag='"3"'),
            ],
            calls,
        ),
    )

    outcome = pull_once(force=True, app_url=_APP)
    pull_once(force=True, app_url=_APP)

    assert outcome.status is PullStatus.REJECTED
    assert release_store.latest_stored_seq() is None
    # No ETag kept, so a binary that later trusts the key fetches the release again.
    assert calls == ["", ""]
