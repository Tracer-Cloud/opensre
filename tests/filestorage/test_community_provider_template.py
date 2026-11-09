"""Extension-path template: how a community ObjectStore backend plugs in.

This file is not testing platform code that changes often - it exists as a
worked example for anyone adding a new cloud backend (an S3-alternative, a
self-hosted blob store, etc.) to remote sync. The whole contract is:

1. Implement the four :class:`~infrastructure.filestorage.contracts.ObjectStore`
   protocol methods: ``list_objects``, ``get_object``, ``put_object``,
   ``describe``.
2. Scope every key through ``config.key_for()`` on write/read, and strip the
   configured prefix back off on list. Every built-in provider does this
   (see ``infrastructure/filestorage/providers/_s3_shared.py``'s ``_strip_prefix``,
   used by ``aws.py``/``s3compat.py``, and the same pattern in
   ``gcs.py``/``azure.py``/``vercel.py``) so two configs sharing one bucket
   under different prefixes do not collide.
3. Record each object's real write time and return that same value on every
   subsequent listing, never a value computed fresh inside ``list_objects``.
   The engine's push/pull compare ``last_modified`` to decide which side is
   newer (:func:`~infrastructure.filestorage.engine.push`,
   :func:`~infrastructure.filestorage.engine._should_download`); a timestamp that
   changes on every listing call breaks that comparison silently.
4. Protect any shared state ``put_object`` mutates with a lock. A push calls
   it concurrently - up to ``max_parallel_uploads`` at once, a cap the
   provider declares to :func:`~infrastructure.filestorage.providers.registry.register_object_store`
   (see the concurrency note on :meth:`~infrastructure.filestorage.contracts.ObjectStore.put_object`
   itself) - so an implementation that mutates shared state unprotected can
   silently lose writes under a real multi-file push.
5. Call :func:`~infrastructure.filestorage.providers.registry.register_object_store`
   with a provider name and a factory - typically at import time in your own
   provider module, the way ``infrastructure/filestorage/providers/aws.py`` does.
6. Nothing else changes: ``RemoteSyncConfig(provider="your-name", ...)`` plus
   :func:`~infrastructure.filestorage.providers.registry.build_object_store` resolve
   to your factory automatically - the engine, CLI, and REPL never import a
   vendor module directly. :func:`~infrastructure.filestorage.engine.push` and
   :func:`~infrastructure.filestorage.engine.pull` talk only to the ``ObjectStore``
   protocol, so a new backend gets the full sync engine for free, exercised
   below with real temp directories rather than mocks.
7. Tests unregister what they registered (see the ``fake_provider`` fixture)
   so a test-only provider never leaks into another test file that calls
   :func:`~infrastructure.filestorage.providers.registry.registered_providers` or
   ``build_object_store``.

See ``infrastructure/filestorage/providers/aws.py`` for the same steps against a
real SDK.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.contracts import RemoteObject
from infrastructure.filestorage.engine import content_tag, pull, push
from infrastructure.filestorage.enums import SyncRootName
from infrastructure.filestorage.providers.registry import (
    build_object_store,
    register_object_store,
    unregister_object_store,
)
from infrastructure.filestorage.syncable import SyncRoot

_PROVIDER_NAME = "fake"


class _FakeObjectStore:
    """The minimum a community backend implements, scoped like a real bucket.

    ``bucket`` is shared across every ``_FakeObjectStore`` instance built for
    it (a plain dict standing in for one cloud bucket), the way a real bucket
    is shared by every client pointed at it regardless of which prefix each
    one is configured with. Each instance only ever sees the slice under its
    own ``config.prefix``, via ``config.key_for()`` on write and prefix
    stripping on list, matching every built-in provider.

    ``_lock`` guards every read and write of ``bucket``: a push calls
    ``put_object`` concurrently (see step 4 in the module docstring), and a
    provider that skips this would risk losing a write under a real
    multi-file push even though a single-file test would never catch it.
    ``barrier`` is a test-only hook (see
    ``test_put_object_is_safe_under_the_engines_real_concurrent_push`` below)
    that forces genuine overlap between calls; a real provider never needs one.
    """

    def __init__(
        self,
        config: RemoteSyncConfig,
        *,
        bucket: dict[str, tuple[bytes, datetime]],
        barrier: threading.Barrier | None = None,
    ) -> None:
        self._config = config
        self._bucket = bucket
        self._lock = threading.Lock()
        self._barrier = barrier

    def _prefix(self) -> str:
        return f"{self._config.prefix.rstrip('/')}/"

    def _strip_prefix(self, full_key: str) -> str:
        prefix = self._prefix()
        return full_key[len(prefix) :] if full_key.startswith(prefix) else full_key

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        full_prefix = self._config.key_for(prefix) if prefix else self._prefix()
        with self._lock:
            snapshot = list(self._bucket.items())
        return [
            RemoteObject(
                key=self._strip_prefix(full_key),
                size=len(data),
                last_modified=written_at,
                etag=content_tag(data),
            )
            for full_key, (data, written_at) in snapshot
            if full_key.startswith(full_prefix)
        ]

    def get_object(self, key: str) -> bytes:
        with self._lock:
            data, _ = self._bucket[self._config.key_for(key)]
        return data

    def put_object(self, key: str, data: bytes) -> None:
        if self._barrier is not None:
            self._barrier.wait(timeout=5.0)
        with self._lock:
            # The write time is captured once, here, under the same lock as
            # the write itself, and never recomputed on a later
            # list_objects() call - see step 3 in the module docstring.
            self._bucket[self._config.key_for(key)] = (data, datetime.now(tz=UTC))

    def describe(self) -> str:
        return f"{_PROVIDER_NAME}://template/{self._config.prefix}"


@pytest.fixture
def fake_provider() -> Iterator[dict[str, tuple[bytes, datetime]]]:
    """Registers "fake" for one test and unregisters it afterward.

    Mirrors how a real provider module registers a factory at import time
    (``infrastructure/filestorage/providers/aws.py``'s
    ``register_object_store(PROVIDER_NAME, _factory, ...)``), scoped to a
    single test so no fake store leaks into ``build_object_store`` for any
    other test file. Yields the backing ``bucket`` dict so a test can build
    more than one ``_FakeObjectStore`` (different prefixes) against the same
    underlying data, the way two clients share one real bucket.
    """
    bucket: dict[str, tuple[bytes, datetime]] = {}
    register_object_store(_PROVIDER_NAME, lambda config: _FakeObjectStore(config, bucket=bucket))
    yield bucket
    unregister_object_store(_PROVIDER_NAME)


def test_a_registered_fake_provider_is_built_via_the_registry(
    fake_provider: dict[str, tuple[bytes, datetime]],
) -> None:
    # Arrange
    config = RemoteSyncConfig(bucket="template-bucket", provider=_PROVIDER_NAME)

    # Act
    built = build_object_store(config)

    # Assert
    assert isinstance(built, _FakeObjectStore)
    assert built.describe() == f"fake://template/{config.prefix}"


def test_push_then_pull_round_trips_a_file_through_the_fake_store(
    fake_provider: dict[str, tuple[bytes, datetime]], tmp_path: Path
) -> None:
    """Full push/pull round trip against the engine, the way a real sync runs."""
    # Arrange
    config = RemoteSyncConfig(bucket="template-bucket", provider=_PROVIDER_NAME, prefix="laptop-1")
    store = build_object_store(config)
    push_root = tmp_path / "push_side" / "sessions"
    pull_root = tmp_path / "pull_side" / "sessions"
    push_root.mkdir(parents=True)
    pull_root.mkdir(parents=True)
    (push_root / "example.jsonl").write_text('{"turn": 1}\n', encoding="utf-8")
    push_roots = (SyncRoot(name=SyncRootName.SESSIONS, path=push_root),)
    pull_roots = (SyncRoot(name=SyncRootName.SESSIONS, path=pull_root),)

    # Act
    push_report = push(store, roots=push_roots)
    pull_report = pull(store, roots=pull_roots)

    # Assert
    assert "sessions/example.jsonl" in push_report.uploaded
    assert "sessions/example.jsonl" in pull_report.downloaded
    assert (pull_root / "example.jsonl").read_text(encoding="utf-8") == '{"turn": 1}\n'


def test_objects_are_scoped_under_the_configured_prefix(
    fake_provider: dict[str, tuple[bytes, datetime]],
) -> None:
    """Two configs sharing one bucket under different prefixes must not collide.

    Catches a template that discards ``RemoteSyncConfig`` and operates on raw
    keys: that shortcut would make this test see team-b's object from
    team-a's listing, the same way a real misconfigured provider could leak
    or overwrite another prefix's data in a shared bucket.
    """
    # Arrange
    team_a = build_object_store(
        RemoteSyncConfig(bucket="shared-bucket", provider=_PROVIDER_NAME, prefix="team-a")
    )
    team_b = build_object_store(
        RemoteSyncConfig(bucket="shared-bucket", provider=_PROVIDER_NAME, prefix="team-b")
    )

    # Act
    team_a.put_object("sessions/shared.jsonl", b"from-a")

    # Assert
    assert [obj.key for obj in team_a.list_objects("")] == ["sessions/shared.jsonl"]
    assert team_b.list_objects("") == []


def test_put_object_records_a_stable_write_time_not_a_fresh_one_per_listing(
    fake_provider: dict[str, tuple[bytes, datetime]],
) -> None:
    """Catches a template that computes last_modified inside list_objects.

    The engine's push/pull decide which side is newer by comparing
    ``last_modified`` (see :func:`~infrastructure.filestorage.engine.push` and
    :func:`~infrastructure.filestorage.engine._should_download`). A value
    recomputed on every listing call would make every remote object look
    freshly written on every list, which can make push wrongly keep back a
    local edit that is actually newer, or make pull wrongly re-download an
    object that has not changed.
    """
    # Arrange
    store = build_object_store(RemoteSyncConfig(bucket="b", provider=_PROVIDER_NAME))
    store.put_object("sessions/old.jsonl", b"{}")

    # Act
    first_listing = store.list_objects("")[0].last_modified
    second_listing = store.list_objects("")[0].last_modified

    # Assert
    assert first_listing == second_listing


def test_put_object_is_safe_under_the_engines_real_concurrent_push(tmp_path: Path) -> None:
    """``put_object`` is called concurrently by a real push - see step 4 in
    the module docstring and the concurrency note on
    :meth:`~infrastructure.filestorage.contracts.ObjectStore.put_object` itself - up to
    ``max_parallel_uploads`` objects in flight at once. A barrier, not a mere
    file count, is what proves genuine overlap happened rather than the
    uploads happening to run one after another; the same technique is used
    against the engine itself in ``test_push_concurrency.py``.

    A handful of writes to *distinct* dictionary keys would still pass even
    with the lock deleted entirely - CPython's GIL already makes that one
    pattern safe on its own, so counting uploads or final entries proves
    nothing about the lock. Spying on ``store._lock`` instead proves
    ``put_object`` actually enters it once per call, which is the concrete
    thing "protect shared state" has to mean: deleting the
    ``with self._lock:`` line in ``put_object`` is what this test catches.
    """
    # Arrange
    cap = 4
    barrier = threading.Barrier(cap, timeout=5.0)
    config = RemoteSyncConfig(bucket="b", provider=_PROVIDER_NAME)
    bucket: dict[str, tuple[bytes, datetime]] = {}
    store = _FakeObjectStore(config, bucket=bucket, barrier=barrier)
    spy_lock = MagicMock(wraps=store._lock)
    store._lock = spy_lock
    root = tmp_path / "sessions"
    root.mkdir()
    for i in range(cap):
        (root / f"file{i}.jsonl").write_text(f'{{"n": {i}}}\n', encoding="utf-8")
    roots = (SyncRoot(name=SyncRootName.SESSIONS, path=root),)

    # Act
    report = push(store, roots=roots, max_parallel_uploads=cap)

    # Assert
    assert len(report.uploaded) == cap
    # Bypasses the spied store and reads the backing dict directly, so this
    # count is independent of how many times list_objects() also happens to
    # enter the lock.
    assert len(bucket) == cap
    # At least once per put_object call - exactly cap if nothing else touches
    # the lock, more if push() also lists first. Either way, removing the
    # lock from put_object drops this below cap and fails the assertion.
    assert spy_lock.__enter__.call_count >= cap
