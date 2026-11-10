"""Built-in object stores release the network clients they own; the sync closes its store."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from config.constants.filestorage import (
    REMOTE_SYNC_BUCKET_ENV,
    REMOTE_SYNC_ENV,
    REMOTE_SYNC_PROVIDER_ENV,
)
from infrastructure.filestorage import operations as sync_service
from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.contracts import RemoteObject
from infrastructure.filestorage.engine import SyncReport
from infrastructure.filestorage.operations import run_remote_sync
from infrastructure.filestorage.providers import aws, azure, gcs, s3compat
from infrastructure.filestorage.providers.neon import NeonObjectStore
from infrastructure.filestorage.providers.registry import (
    register_object_store,
    unregister_object_store,
)
from infrastructure.filestorage.providers.vercel import VercelBlobObjectStore

_VERCEL_TOKEN = "vercel_blob_rw_TestStoreId_deadbeefsecret"
_ENDPOINT = "http://localhost:9000"


class _SpyClient:
    """Stands in for a boto3 client or a requests session and records close()."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _SpyHttpxClient(httpx.Client):
    """A real ``httpx.Client`` that also records whether it was closed."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.closed = False

    def close(self) -> None:
        self.closed = True
        super().close()


def _config() -> RemoteSyncConfig:
    return RemoteSyncConfig(bucket="b", provider="test", prefix="p", profile="acct")


def _always(value: Any) -> Callable[..., Any]:
    def _build(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return _build


# Each builder returns (store, client_spy). ``owned=True`` lets the store build its
# own client; ``owned=False`` injects the spy the way a test or caller would.
def _build_aws(mp: pytest.MonkeyPatch, owned: bool) -> tuple[Any, Any]:
    spy = _SpyClient()
    if owned:
        mp.setattr(aws, "_build_client", _always(spy))
        return aws.S3ObjectStore(_config()), spy
    return aws.S3ObjectStore(_config(), client=spy), spy


def _build_s3compat(mp: pytest.MonkeyPatch, owned: bool) -> tuple[Any, Any]:
    spy = _SpyClient()
    if owned:
        mp.setattr(s3compat, "_build_client", _always(spy))
        return s3compat.S3CompatObjectStore(_config(), endpoint_url=_ENDPOINT), spy
    return s3compat.S3CompatObjectStore(_config(), endpoint_url=_ENDPOINT, client=spy), spy


def _build_neon(mp: pytest.MonkeyPatch, owned: bool) -> tuple[Any, Any]:
    # Neon inherits S3CompatObjectStore, so it builds its client through s3compat.
    spy = _SpyClient()
    if owned:
        mp.setattr(s3compat, "_build_client", _always(spy))
        return NeonObjectStore(_config(), endpoint_url=_ENDPOINT), spy
    return NeonObjectStore(_config(), endpoint_url=_ENDPOINT, client=spy), spy


def _build_gcs(mp: pytest.MonkeyPatch, owned: bool) -> tuple[Any, Any]:
    spy = _SpyClient()
    if owned:
        mp.setattr(gcs, "_build_session", _always(spy))
        return gcs.GCSObjectStore(_config()), spy
    return gcs.GCSObjectStore(_config(), session=spy), spy


def _build_azure(mp: pytest.MonkeyPatch, owned: bool) -> tuple[Any, Any]:
    if owned:
        mp.setattr(httpx, "Client", _SpyHttpxClient)
        store = azure.AzureBlobObjectStore(_config(), token="t")
        return store, store._client
    spy = _SpyHttpxClient()
    return azure.AzureBlobObjectStore(_config(), token="t", client=spy), spy


def _build_vercel(mp: pytest.MonkeyPatch, owned: bool) -> tuple[Any, Any]:
    if owned:
        mp.setattr(httpx, "Client", _SpyHttpxClient)
        store = VercelBlobObjectStore(_config(), token=_VERCEL_TOKEN)
        return store, store._client
    spy = _SpyHttpxClient()
    return VercelBlobObjectStore(_config(), token=_VERCEL_TOKEN, client=spy), spy


_BUILDERS: dict[str, Callable[[pytest.MonkeyPatch, bool], tuple[Any, Any]]] = {
    "aws": _build_aws,
    "s3compat": _build_s3compat,
    "neon": _build_neon,
    "gcs": _build_gcs,
    "azure": _build_azure,
    "vercel": _build_vercel,
}


@pytest.mark.parametrize("provider", sorted(_BUILDERS))
def test_close_releases_the_client_the_store_built(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, spy = _BUILDERS[provider](monkeypatch, True)

    store.close()
    store.close()  # a finally block may run it after an earlier cleanup already did

    assert spy.closed


@pytest.mark.parametrize("provider", sorted(_BUILDERS))
def test_close_leaves_an_injected_client_open(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, spy = _BUILDERS[provider](monkeypatch, False)

    store.close()

    assert not spy.closed


class _RecordingStore:
    """The minimum a backend implements, plus a close() the sync should reach."""

    def __init__(self) -> None:
        self.closed = False

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        _ = prefix
        return []

    def get_object(self, key: str) -> bytes:
        return key.encode()

    def put_object(self, key: str, data: bytes) -> None:
        _ = (key, data)

    def describe(self) -> str:
        return "recording://t"

    def close(self) -> None:
        self.closed = True


class _FailingCloseStore(_RecordingStore):
    """Fails to close and, like a store that tore its state down, can no longer describe itself."""

    def describe(self) -> str:
        if self.closed:
            raise RuntimeError("store state already released")
        return super().describe()

    def close(self) -> None:
        super().close()
        raise OSError("socket already gone")


def _empty_run_sync(_store: object, **_kwargs: object) -> SyncReport:
    return SyncReport()


def _failing_run_sync(_store: object, **_kwargs: object) -> SyncReport:
    raise RuntimeError("sync exploded")


@pytest.fixture
def register_store(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[type[_RecordingStore]], list[_RecordingStore]]]:
    """Enable remote sync against a fake provider; return every store the sync builds."""
    names: list[str] = []

    def _register(store_type: type[_RecordingStore]) -> list[_RecordingStore]:
        built: list[_RecordingStore] = []

        def _factory(_cfg: RemoteSyncConfig) -> _RecordingStore:
            store = store_type()
            built.append(store)
            return store

        name = f"close-{len(names)}"
        names.append(name)
        register_object_store(name, _factory)
        monkeypatch.setenv(REMOTE_SYNC_PROVIDER_ENV, name)
        return built

    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "b")
    monkeypatch.setattr(sync_service, "syncable_roots", tuple)
    monkeypatch.setattr(sync_service, "run_sync", _empty_run_sync)
    yield _register
    for name in names:
        unregister_object_store(name)


def test_run_remote_sync_closes_the_store_it_built(
    register_store: Callable[[type[_RecordingStore]], list[_RecordingStore]],
) -> None:
    built = register_store(_RecordingStore)

    report = run_remote_sync()

    assert report is not None
    assert [store.closed for store in built] == [True]


def test_run_remote_sync_closes_the_store_and_reraises_when_the_sync_fails(
    register_store: Callable[[type[_RecordingStore]], list[_RecordingStore]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = register_store(_RecordingStore)
    monkeypatch.setattr(sync_service, "run_sync", _failing_run_sync)

    with pytest.raises(RuntimeError, match="sync exploded"):
        run_remote_sync()

    assert [store.closed for store in built] == [True]


def test_a_failing_close_does_not_fail_a_sync_that_succeeded(
    register_store: Callable[[type[_RecordingStore]], list[_RecordingStore]],
) -> None:
    register_store(_FailingCloseStore)

    report = run_remote_sync()

    assert report is not None


def test_a_failing_close_does_not_replace_the_sync_error(
    register_store: Callable[[type[_RecordingStore]], list[_RecordingStore]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_store(_FailingCloseStore)
    monkeypatch.setattr(sync_service, "run_sync", _failing_run_sync)

    with pytest.raises(RuntimeError, match="sync exploded"):
        run_remote_sync()
