"""Push uploads in parallel without changing what a push means.

Two properties are load-bearing here and neither is visible from a green
sequential suite: the plan is still closed before the first byte is sent (the
deny-list is a security boundary, so a refusal must find an empty store), and
the report a caller reads back does not depend on which upload finished first.

The concurrency is exercised with a barrier rather than sleeps: a barrier of
``cap`` parties only trips if that many uploads are genuinely in flight, so the
test proves the pool is wide instead of hoping a timing margin holds on a busy
CI box.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from config.constants.filestorage import DEFAULT_MAX_PARALLEL_UPLOADS
from infrastructure.filestorage.contracts import RemoteObject
from infrastructure.filestorage.engine import SyncProgress, content_tag, push
from infrastructure.filestorage.enums import SyncDirection, SyncRootName
from infrastructure.filestorage.errors import UnsyncablePathError
from infrastructure.filestorage.syncable import SyncRoot

LEAKED_SECRET = "sk-live-CANARY-must-never-reach-the-bucket"
_BARRIER_TIMEOUT_SECONDS = 5.0


class RecordingStore:
    """In-memory store that records how many uploads overlap.

    ``max_in_flight`` is the high-water mark of concurrent ``put_object``
    calls, and ``upload_order`` is the order they actually completed in —
    which the report must *not* inherit.
    """

    def __init__(self, *, barrier: threading.Barrier | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self.upload_order: list[str] = []
        self.max_in_flight = 0
        self._in_flight = 0
        self._lock = threading.Lock()
        self._barrier = barrier

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
        with self._lock:
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            if self._barrier is not None:
                # Only returns once ``parties`` uploads are here at once.
                self._barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        finally:
            with self._lock:
                self._in_flight -= 1
                self.objects[key] = data
                self.upload_order.append(key)

    def describe(self) -> str:
        return "recording://bucket"


class FailingStore(RecordingStore):
    """Fails the named keys, to pin which error a caller sees."""

    def __init__(self, *, failing: set[str]) -> None:
        super().__init__()
        self._failing = failing

    def put_object(self, key: str, data: bytes) -> None:
        if key in self._failing:
            raise RemoteSyncTestError(key)
        super().put_object(key, data)


class RemoteSyncTestError(RuntimeError):
    """Stand-in for a provider write failure, carrying the key that failed."""


def _write_tree(root: Path, count: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (root / f"s{index:03d}.jsonl").write_text(f'{{"turn": {index}}}\n', encoding="utf-8")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A laptop tree with credential files sitting beside the synced roots."""
    _write_tree(tmp_path / "sessions", 8)
    _write_tree(tmp_path / "memory", 4)
    (tmp_path / "integrations.json").write_text(
        f'{{"datadog": {{"api_key": "{LEAKED_SECRET}"}}}}', encoding="utf-8"
    )
    (tmp_path / "llm-auth.json").write_text(f'{{"openai": "{LEAKED_SECRET}"}}', encoding="utf-8")
    return tmp_path


@pytest.fixture
def roots(home: Path) -> tuple[SyncRoot, ...]:
    return (
        SyncRoot(name=SyncRootName.SESSIONS, path=home / "sessions"),
        SyncRoot(name=SyncRootName.MEMORY, path=home / "memory"),
    )


# ── Bounded concurrency ─────────────────────────────────────────────────────


def test_uploads_really_do_overlap_up_to_the_cap(roots: tuple[SyncRoot, ...]) -> None:
    """A barrier of ``cap`` parties trips only if that many uploads overlap."""
    # Arrange: 12 candidates, a cap of 4, and a barrier needing 4 at once.
    cap = 4
    store = RecordingStore(barrier=threading.Barrier(cap))

    # Act
    report = push(store, roots=roots, max_parallel_uploads=cap)

    # Assert: the barrier never timed out, so 4 were genuinely in flight.
    assert len(report.uploaded) == 12
    assert store.max_in_flight == cap


def test_the_pool_never_exceeds_the_declared_cap(roots: tuple[SyncRoot, ...]) -> None:
    """A provider's declared limit is a ceiling, not a hint."""
    # Arrange
    cap = 2
    store = RecordingStore(barrier=threading.Barrier(cap))

    # Act
    push(store, roots=roots, max_parallel_uploads=cap)

    # Assert
    assert store.max_in_flight <= cap


def test_a_single_candidate_needs_no_pool(tmp_path: Path) -> None:
    """One file must not pay for thread setup — and must still upload."""
    # Arrange
    _write_tree(tmp_path / "sessions", 1)
    one_root = (SyncRoot(name=SyncRootName.SESSIONS, path=tmp_path / "sessions"),)
    store = RecordingStore()

    # Act
    report = push(store, roots=one_root, max_parallel_uploads=8)

    # Assert
    assert report.uploaded == ["sessions/s000.jsonl"]
    assert store.max_in_flight == 1


# ── The report does not inherit completion order ────────────────────────────


def test_the_report_stays_in_plan_order_however_uploads_interleave(
    roots: tuple[SyncRoot, ...],
) -> None:
    """Uploads finish in whatever order; ``uploaded`` is the order pushed."""
    # Arrange: the barrier releases threads in an order nobody controls.
    store = RecordingStore(barrier=threading.Barrier(4))

    # Act
    report = push(store, roots=roots, max_parallel_uploads=4)

    # Assert: plan order is roots in order, files sorted within each root.
    expected = [f"sessions/s{index:03d}.jsonl" for index in range(8)]
    expected += [f"memory/s{index:03d}.jsonl" for index in range(4)]
    assert report.uploaded == expected
    # Completion order is a real permutation of it, not the same list — proof
    # the ordering above is imposed by the fold, not by luck.
    assert sorted(store.upload_order) == sorted(expected)


def test_counters_do_not_lose_updates_under_a_wide_fan_out(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    """``skipped`` and ``uploaded_bytes`` are read-modify-write — the classic loss."""
    # Arrange: first push fills the store, so the second sees every file as
    # unchanged and takes the ``skipped`` path 12 times concurrently.
    store = RecordingStore()
    first = push(store, roots=roots, max_parallel_uploads=8)

    # Act
    second = push(store, roots=roots, max_parallel_uploads=8)

    # Assert
    assert first.uploaded_bytes == sum(len(data) for data in store.objects.values())
    assert second.skipped == 12
    assert second.uploaded == []
    assert second.uploaded_bytes == 0


def test_the_parallel_report_equals_the_sequential_one(roots: tuple[SyncRoot, ...]) -> None:
    """Same inputs, same report — the pool width is not observable."""
    # Arrange / Act
    sequential = push(RecordingStore(), roots=roots, max_parallel_uploads=1)
    parallel = push(RecordingStore(), roots=roots, max_parallel_uploads=8)

    # Assert
    assert sequential.uploaded == parallel.uploaded
    assert sequential.uploaded_bytes == parallel.uploaded_bytes
    assert sequential.skipped == parallel.skipped
    assert sequential.kept_remote == parallel.kept_remote


# ── Progress stays a single-threaded, in-order callback ─────────────────────


def test_progress_fires_once_per_candidate_in_order_on_the_calling_thread(
    roots: tuple[SyncRoot, ...],
) -> None:
    """A progress bar is not thread-safe, so callbacks must not run on workers."""
    # Arrange
    calling_thread = threading.get_ident()
    seen: list[SyncProgress] = []
    threads: set[int] = set()

    def _record(progress: SyncProgress) -> None:
        seen.append(progress)
        threads.add(threading.get_ident())

    # Act
    push(RecordingStore(), roots=roots, max_parallel_uploads=8, on_progress=_record)

    # Assert
    assert threads == {calling_thread}
    assert [progress.completed for progress in seen] == list(range(1, 13))
    assert all(progress.total == 12 for progress in seen)
    assert all(progress.direction is SyncDirection.PUSH for progress in seen)


# ── Validate-then-transfer survives the pool ────────────────────────────────


def test_a_denied_file_uploads_nothing_even_with_a_wide_pool(home: Path) -> None:
    """The refusal must beat every upload, not race the first few.

    The second root reaches the credential files. A pool that started
    uploading while the walk continued would already have written the first
    root by the time the refusal fires, so an empty store is the proof.
    """
    # Arrange
    roots_with_a_bad_one = (
        SyncRoot(name=SyncRootName.SESSIONS, path=home / "sessions"),
        SyncRoot(name="everything", path=home),
    )
    store = RecordingStore()

    # Act
    with pytest.raises(UnsyncablePathError):
        push(store, roots=roots_with_a_bad_one, max_parallel_uploads=8)

    # Assert
    assert store.objects == {}
    assert store.upload_order == []


def test_no_stored_object_carries_the_canary_under_parallel_push(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    """The deny-list holds when uploads overlap."""
    # Arrange
    store = RecordingStore()

    # Act
    push(store, roots=roots, max_parallel_uploads=8)

    # Assert
    assert store.objects
    for key, body in store.objects.items():
        assert LEAKED_SECRET.encode() not in body, f"secret leaked into {key}"


# ── Failure and dry-run ─────────────────────────────────────────────────────


def test_a_failing_upload_raises_the_earliest_failure_by_plan_order(
    roots: tuple[SyncRoot, ...],
) -> None:
    """Which error surfaces must not depend on which thread lost the race."""
    # Arrange: two keys fail; the earlier one in plan order must be reported.
    store = FailingStore(failing={"sessions/s002.jsonl", "sessions/s005.jsonl"})

    # Act
    with pytest.raises(RemoteSyncTestError) as caught:
        push(store, roots=roots, max_parallel_uploads=8)

    # Assert
    assert caught.value.args[0] == "sessions/s002.jsonl"


def test_a_dry_run_sends_nothing_and_starts_no_threads(roots: tuple[SyncRoot, ...]) -> None:
    """Nothing is uploaded, so there is no latency to overlap."""
    # Arrange
    store = RecordingStore()

    # Act
    report = push(store, roots=roots, max_parallel_uploads=8, dry_run=True)

    # Assert
    assert store.objects == {}
    assert store.max_in_flight == 0
    assert len(report.uploaded) == 12


def test_an_empty_plan_is_a_no_op(tmp_path: Path) -> None:
    """No candidates means no pool and no report entries."""
    # Arrange
    (tmp_path / "sessions").mkdir()
    empty = (SyncRoot(name=SyncRootName.SESSIONS, path=tmp_path / "sessions"),)
    store = RecordingStore()

    # Act
    report = push(store, roots=empty, max_parallel_uploads=DEFAULT_MAX_PARALLEL_UPLOADS)

    # Assert
    assert report.uploaded == []
    assert store.max_in_flight == 0
