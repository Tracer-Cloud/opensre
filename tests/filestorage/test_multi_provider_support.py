"""Prove remote sync supports more than one cloud without shipping every SDK.

Built-in backends (aws, gcs, vercel, azure) register via ``_BUILTIN_MODULES``.
Community providers register the same way these fakes do - the engine and
factory never change.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.contracts import RemoteObject
from infrastructure.filestorage.engine import content_tag, run_sync
from infrastructure.filestorage.enums import SyncRootName
from infrastructure.filestorage.errors import RemoteSyncConfigError
from infrastructure.filestorage.providers.registry import (
    build_object_store,
    register_object_store,
    registered_providers,
)
from infrastructure.filestorage.syncable import SyncRoot


@pytest.fixture(autouse=True)
def _restore_provider_registry() -> Iterator[None]:
    """Community-style registrations in these tests must not leak into others."""
    from infrastructure.filestorage.providers import registry as reg

    with reg._REGISTRY_LOCK:
        snapshot = dict(reg._REGISTRY)
    yield
    with reg._REGISTRY_LOCK:
        reg._REGISTRY.clear()
        reg._REGISTRY.update(snapshot)


class _MemoryStore:
    """Minimal ObjectStore used as a stand-in for a future cloud backend."""

    def __init__(self, *, label: str) -> None:
        self.label = label
        self.objects: dict[str, bytes] = {}

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        return [
            RemoteObject(
                key=key,
                size=len(data),
                last_modified=datetime.now(tz=UTC),
                etag=content_tag(data),
            )
            for key, data in self.objects.items()
            if key.startswith(prefix)
        ]

    def get_object(self, key: str) -> bytes:
        return self.objects[key]

    def put_object(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def describe(self) -> str:
        return f"{self.label}://test"


@pytest.fixture
def roots(tmp_path: Path) -> tuple[SyncRoot, ...]:
    sessions = tmp_path / "sessions"
    memory = tmp_path / "memory"
    sessions.mkdir()
    memory.mkdir()
    (sessions / "a.jsonl").write_bytes(b'{"turn": 1}\n')
    (memory / "fact.md").write_bytes(b"remembered\n")
    return (
        SyncRoot(name=SyncRootName.SESSIONS, path=sessions),
        SyncRoot(name=SyncRootName.MEMORY, path=memory),
    )


def test_built_in_providers_include_aws_gcs_vercel_and_azure() -> None:
    providers = registered_providers()
    assert "aws" in providers
    assert "gcs" in providers
    assert "vercel" in providers
    assert "azure" in providers
    assert "s3compat" in providers


def test_unknown_provider_fails_closed_listing_known_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config.constants.filestorage import (
        REMOTE_SYNC_BUCKET_ENV,
        REMOTE_SYNC_ENV,
        REMOTE_SYNC_PROVIDER_ENV,
    )
    from infrastructure.filestorage import load_remote_sync_config

    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "b")
    monkeypatch.setenv(REMOTE_SYNC_PROVIDER_ENV, "azure-blob")

    config = load_remote_sync_config()
    assert config is not None
    with pytest.raises(RemoteSyncConfigError, match="unknown remote-sync provider") as caught:
        build_object_store(config)
    # Operator sees what is installed today so they know to add a module.
    assert "aws" in str(caught.value)
    assert "gcs" in str(caught.value)
    assert "vercel" in str(caught.value)


def test_community_style_gcs_registration_drives_the_engine(
    roots: tuple[SyncRoot, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A community registration overrides the built-in of the same name.

    GCS is built in now; registering a stand-in under the same name must win
    without a lazy built-in import replacing it — the open/closed seam a third
    party uses to ship a fix without forking.
    """
    from config.constants.filestorage import (
        REMOTE_SYNC_BUCKET_ENV,
        REMOTE_SYNC_ENV,
        REMOTE_SYNC_PROVIDER_ENV,
    )
    from infrastructure.filestorage import load_remote_sync_config

    store = _MemoryStore(label="gs")
    register_object_store("gcs", lambda _cfg: store)

    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "community-bucket")
    monkeypatch.setenv(REMOTE_SYNC_PROVIDER_ENV, "gcs")

    built = build_object_store(load_remote_sync_config())  # type: ignore[arg-type]
    report = run_sync(built, roots=roots)

    assert built is store
    assert built.describe().startswith("gs://")
    assert "sessions/a.jsonl" in report.uploaded
    assert store.objects["sessions/a.jsonl"] == b'{"turn": 1}\n'


def test_community_style_azure_registration_drives_the_engine(
    roots: tuple[SyncRoot, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    from config.constants.filestorage import (
        REMOTE_SYNC_BUCKET_ENV,
        REMOTE_SYNC_ENV,
        REMOTE_SYNC_PROVIDER_ENV,
    )
    from infrastructure.filestorage import load_remote_sync_config

    store = _MemoryStore(label="azure")
    register_object_store("azure", lambda _cfg: store)

    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "community-container")
    monkeypatch.setenv(REMOTE_SYNC_PROVIDER_ENV, "azure")

    built = build_object_store(load_remote_sync_config())  # type: ignore[arg-type]
    report = run_sync(built, roots=roots)

    assert built is store
    assert "memory/fact.md" in report.uploaded
    assert store.get_object("memory/fact.md") == b"remembered\n"


def test_two_registered_providers_are_selected_independently(
    roots: tuple[SyncRoot, ...],
) -> None:
    gcs_store = _MemoryStore(label="gs")
    azure_store = _MemoryStore(label="azure")
    register_object_store("gcs", lambda _cfg: gcs_store)
    register_object_store("azure", lambda _cfg: azure_store)

    assert build_object_store(RemoteSyncConfig(bucket="b", provider="gcs")) is gcs_store
    assert build_object_store(RemoteSyncConfig(bucket="c", provider="azure")) is azure_store

    run_sync(gcs_store, roots=roots)
    run_sync(azure_store, roots=roots)
    assert gcs_store.objects.keys() == azure_store.objects.keys()
