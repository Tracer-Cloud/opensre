"""Cross-process soak matrix for the session file lock (#5497).

The same-process tests in ``test_jsonl_store_lock.py`` cannot distinguish a
working lock from one that merely appears to work: a lock's happy path looks
identical either way. These four cases run real writer processes, because the
crash case needs a process the OS can kill and the concurrency cases need
writers that are not sharing an interpreter lock.

Run with ``-s`` to see the per-run lock-wait, timeout, and decode-failure
numbers; they are printed for every case and are the signal worth watching
while the lock is young.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from filelock import FileLock

from config.constants import OPENSRE_HOME_ENV, OPENSRE_OPERATIONS_LOG_PATH_ENV
from config.constants.session_store import OPENSRE_SESSION_FILE_LOCK_ENV
from core.agent_harness.session import JsonlSessionRepo, JsonlSessionStore
from core.agent_harness.session.persistence.paths import session_path, sessions_dir
from infrastructure.observability.operations_log import read_operations

_WORKER = Path(__file__).with_name("_lock_soak_worker.py")
_REPO_ROOT = Path(__file__).resolve().parents[4]

_PROCESS_TIMEOUT_SECONDS = 60.0
_REAP_TIMEOUT_SECONDS = 10.0
# How long released writers contend once all of them are writing. Bounds the
# soak; it never bounds correctness, because the parent ends the run.
_SOAK_SECONDS = 1.0
# The victim holds the lock for far longer than the parent needs to kill it,
# so the kill can never land after it has let go.
_VICTIM_SECONDS = 30.0
# The OS frees a dead holder's flock immediately; this is slack, not a wait.
_LOCK_RECLAIM_SECONDS = 5.0
# Short on purpose: a writer that should not be contending must fail fast
# rather than stall the suite for the store's generous default.
_CONTENDED_TIMEOUT_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class LockMetrics:
    """What one soak run cost, as the issue asks it be reported."""

    wait_max_ms: int
    wait_p50_ms: int
    wait_samples: int
    timeouts: int
    decode_failures: int

    def report(self, case: str) -> None:
        print(
            f"\n[soak:{case}] lock-wait max={self.wait_max_ms}ms p50={self.wait_p50_ms}ms "
            f"samples={self.wait_samples} timeouts={self.timeouts} "
            f"decode_failures={self.decode_failures}"
        )


@dataclass(frozen=True, slots=True)
class WorkerResult:
    """One writer process's own account of what it wrote and when."""

    worker_id: str
    pid: int
    first_write_ts: float
    last_write_ts: float
    written: tuple[str, ...]


@pytest.fixture
def soak_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point session storage and the operations log at a temp home."""
    monkeypatch.setenv(OPENSRE_HOME_ENV, str(tmp_path))
    monkeypatch.setenv(OPENSRE_SESSION_FILE_LOCK_ENV, "1")
    monkeypatch.setenv(OPENSRE_OPERATIONS_LOG_PATH_ENV, str(tmp_path / "operations.jsonl"))
    from config.constants import paths as paths_constants

    monkeypatch.setattr(paths_constants, "OPENSRE_HOME_DIR", tmp_path, raising=False)
    return tmp_path


def _session(session_id: str) -> Any:
    return SimpleNamespace(
        session_id=session_id,
        started_at=0.0,
        agent=SimpleNamespace(messages=[]),
        accumulated_context={},
    )


def _seed(session_id: str) -> Path:
    """Create the session file the workers append to.

    The parent seeds it because ``open_session`` opens with ``"w"`` and would
    truncate a session a sibling worker is already writing (#5474).
    """
    JsonlSessionStore().open_session(_session(session_id))
    return session_path(session_id)


def _worker_env(home: Path) -> dict[str, str]:
    return {
        **os.environ,
        OPENSRE_HOME_ENV: str(home),
        OPENSRE_SESSION_FILE_LOCK_ENV: "1",
        OPENSRE_OPERATIONS_LOG_PATH_ENV: str(home / "operations.jsonl"),
        "PYTHONPATH": str(_REPO_ROOT),
    }


@pytest.fixture
def spawn(soak_home: Path) -> Iterator[Callable[[dict[str, Any]], subprocess.Popen[bytes]]]:
    """Spawn workers, and kill any that outlive the test.

    Teardown rather than the test body, because a worker in stop-file mode
    waits for a marker the parent may never write: any failure between spawn
    and the stop signal — a failed handshake, an assertion, a KeyboardInterrupt
    — would otherwise leave it appending turns forever on the CI runner.
    """
    started: list[subprocess.Popen[bytes]] = []

    def _spawn(config: dict[str, Any]) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [sys.executable, str(_WORKER), json.dumps(config)],
            cwd=str(_REPO_ROOT),
            env=_worker_env(soak_home),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        started.append(process)
        return process

    try:
        yield _spawn
    finally:
        for process in started:
            if process.poll() is None:
                process.kill()
            # Always drain: the pipes are still open on a killed worker, and an
            # unread PIPE is a file descriptor this process keeps.
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.communicate(timeout=_REAP_TIMEOUT_SECONDS)


def _await_all(processes: list[subprocess.Popen[bytes]], markers: list[Path], what: str) -> None:
    """Block until every marker exists, failing fast if a worker dies first."""
    deadline = time.monotonic() + _PROCESS_TIMEOUT_SECONDS
    while not all(marker.exists() for marker in markers):
        if time.monotonic() > deadline:
            missing = [marker.name for marker in markers if not marker.exists()]
            pytest.fail(f"workers did not {what}: {missing}")
        assert all(process.poll() is None for process in processes), (
            f"a worker exited before it could {what}"
        )
        time.sleep(0.01)


def _release(
    processes: list[subprocess.Popen[bytes]],
    result_paths: list[Path],
    go: Path,
    stop: Path | None = None,
) -> None:
    """Run every worker concurrently, then stop them all at the same instant.

    Neither end of the window is left to the scheduler. Startup is ~0.4s idle
    and unbounded under load, so workers are released together rather than at a
    wall-clock time; and each one is held open until *every* worker has a turn
    on disk, so a starved worker cannot measure its window after its siblings
    have already exited. Overlap is then structural, not a property of the
    runner.
    """
    _await_all(processes, [Path(f"{path}.ready") for path in result_paths], "signal ready")
    go.touch()
    if stop is None:
        return
    _await_all(processes, [Path(f"{path}.writing") for path in result_paths], "write a first turn")
    # Every worker is now provably writing. Let them contend for a while before
    # calling stop, so the run is a soak and not just a handshake.
    time.sleep(_SOAK_SECONDS)
    stop.touch()


def _await_marker(marker: Path, process: subprocess.Popen[bytes], failure: str) -> None:
    """Block until ``marker`` appears, failing fast if the process dies first."""
    deadline = time.monotonic() + _PROCESS_TIMEOUT_SECONDS
    while not marker.exists():
        if process.poll() is not None:
            pytest.fail(f"{failure} (exited {process.returncode})")
        if time.monotonic() > deadline:
            pytest.fail(f"{failure} (timed out after {_PROCESS_TIMEOUT_SECONDS}s)")
        time.sleep(0.01)


def _await(processes: list[subprocess.Popen[bytes]]) -> None:
    """Wait for every worker, failing loudly on a non-zero exit."""
    deadline = time.monotonic() + _PROCESS_TIMEOUT_SECONDS
    for process in processes:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            _stdout, stderr = process.communicate(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()
            pytest.fail(f"worker {process.pid} did not finish within {_PROCESS_TIMEOUT_SECONDS}s")
        assert process.returncode == 0, f"worker {process.pid} failed: {stderr.decode()}"


def _results(paths: list[Path]) -> list[WorkerResult]:
    return [
        WorkerResult(
            worker_id=str(raw["worker_id"]),
            pid=int(raw["pid"]),
            first_write_ts=float(raw["first_write_ts"]),
            last_write_ts=float(raw["last_write_ts"]),
            written=tuple(raw["written"]),
        )
        for raw in (json.loads(path.read_text(encoding="utf-8")) for path in paths)
    ]


def _metrics(home: Path) -> LockMetrics:
    records = read_operations(path=home / "operations.jsonl", limit=100_000)
    waits = [int(r["data"]["wait_ms"]) for r in records if r["event"] == "session_file_lock_wait"]
    return LockMetrics(
        wait_max_ms=max(waits, default=0),
        wait_p50_ms=int(statistics.median(waits)) if waits else 0,
        wait_samples=len(waits),
        timeouts=sum(1 for r in records if r["event"] == "session_file_lock_timeout"),
        decode_failures=sum(1 for r in records if r["event"] == "session_jsonl_decode_failed"),
    )


def _assert_readable(path: Path) -> list[dict[str, Any]]:
    """Assert the file is intact, tolerating one truncated final line.

    A crash mid-append can leave a partial last record. Anything unparseable
    *before* the last line is interleaving damage, which is what the lock exists
    to prevent.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    records: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            is_final = index == len(lines) - 1
            assert is_final, f"torn line {index + 1} of {len(lines)} is not the final line"

    headers = [r for r in records if r.get("type") == "session"]
    assert len(headers) == 1, f"expected exactly one session header, found {len(headers)}"
    assert records[0].get("type") == "session", "session header is not the first record"
    return records


def _written_markers(records: list[dict[str, Any]]) -> set[str]:
    """Marker prefixes (``w0-3``) of every turn stub in the file."""
    return {
        str(record.get("text", "")).split(":", 1)[0]
        for record in records
        if record.get("custom_type") == "turn_stub"
    }


def test_soak_same_session_writers_serialize_without_loss(
    soak_home: Path, spawn: Callable[[dict[str, Any]], subprocess.Popen[bytes]]
) -> None:
    """Case 1: concurrent writers to one session lose no turn and tear no line."""
    session_id = "soak-same"
    path = _seed(session_id)
    go = soak_home / "go-same"

    result_paths = [soak_home / f"result-{i}.json" for i in range(4)]
    processes = [
        spawn(
            {
                "worker_id": f"w{i}",
                "session_ids": [session_id],
                "turns": 25,
                "result_path": str(result_paths[i]),
                "go_path": str(go),
            },
        )
        for i in range(4)
    ]
    _release(processes, result_paths, go)
    _await(processes)

    records = _assert_readable(path)
    metrics = _metrics(soak_home)
    metrics.report("same-session")

    expected = {marker.split("/", 1)[1] for r in _results(result_paths) for marker in r.written}
    missing = expected - _written_markers(records)
    assert not missing, f"{len(missing)} turns reported written but absent from the file: {missing}"


def test_soak_different_sessions_write_concurrently(
    soak_home: Path, spawn: Callable[[dict[str, Any]], subprocess.Popen[bytes]]
) -> None:
    """Case 2: a per-path lock must not serialize unrelated sessions.

    The overlap is asserted, not assumed: a global lock would still produce a
    green run here, just a serial one, so the test would silently degrade into
    proving nothing.
    """
    session_ids = [f"soak-diff-{i}" for i in range(4)]
    paths = [_seed(session_id) for session_id in session_ids]
    go = soak_home / "go-diff"
    stop = soak_home / "stop-diff"

    result_paths = [soak_home / f"result-{i}.json" for i in range(len(session_ids))]
    # Released together and stopped together by the parent, with every worker
    # held open until all of them have written: the overlap the case asserts is
    # then guaranteed by construction rather than by the scheduler.
    processes = [
        spawn(
            {
                "worker_id": f"w{i}",
                "session_ids": [session_id],
                "stop_path": str(stop),
                "result_path": str(result_paths[i]),
                "go_path": str(go),
            },
        )
        for i, session_id in enumerate(session_ids)
    ]
    _release(processes, result_paths, go, stop)
    _await(processes)

    for path in paths:
        _assert_readable(path)
    metrics = _metrics(soak_home)
    metrics.report("different-sessions")

    results = _results(result_paths)
    latest_start = max(r.first_write_ts for r in results)
    earliest_end = min(r.last_write_ts for r in results)
    assert earliest_end > latest_start, (
        "writers to different sessions did not run at the same time, so this run "
        "measured nothing: "
        + ", ".join(
            f"{r.worker_id}[{r.first_write_ts:.3f}..{r.last_write_ts:.3f}]" for r in results
        )
    )

    # Overlapping wall-clock ranges are necessary but not sufficient. A single
    # global lock still lets writers interleave turn by turn, so the check above
    # passes under one — verified by rebuilding the store with one shared lock
    # and watching this case stay green on timing alone. What a global lock
    # cannot fake is the shape on disk: one lock file per session path, and no
    # lock file shared between them.
    lock_files = {path.name for path in sessions_dir().glob("*.lock")}
    assert lock_files == {f"{path.name}.lock" for path in paths}, (
        "expected one lock file per session and nothing else; the lock is not "
        f"keyed by path, so unrelated sessions serialize against each other: {sorted(lock_files)}"
    )

    # And behaviourally, holding one session's own lock must not stall another.
    blocked_result = soak_home / "unrelated.json"
    held = FileLock(f"{paths[0]}.lock", timeout=_LOCK_RECLAIM_SECONDS)
    held.acquire()
    try:
        _await(
            [
                spawn(
                    {
                        "worker_id": "unrelated",
                        "session_ids": [session_ids[1]],
                        "turns": 3,
                        "result_path": str(blocked_result),
                        "lock_timeout_seconds": _CONTENDED_TIMEOUT_SECONDS,
                    },
                )
            ]
        )
    finally:
        held.release()

    unrelated = _results([blocked_result])[0]
    expected = {marker.split("/", 1)[1] for marker in unrelated.written}
    present = _written_markers(_assert_readable(paths[1]))
    assert expected <= present, (
        f"holding {session_ids[0]}'s lock blocked writes to {session_ids[1]}: the "
        "lock is not per-path, so unrelated sessions serialize against each other"
    )


def test_soak_sigkill_while_holding_the_lock_strands_nothing(
    soak_home: Path, spawn: Callable[[dict[str, Any]], subprocess.Popen[bytes]]
) -> None:
    """Case 3: a hard kill of the lock *holder* leaves the lock takeable.

    SIGKILL, not SIGTERM — a clean shutdown releases the lock through Python and
    proves nothing about a process that never runs its handlers.

    The victim signals from inside the locked region and the parent waits for
    that signal, because a fixed sleep only proves the process is alive: on a
    loaded runner it may still be importing, and killing a victim that never
    took the lock would let this pass while reclaiming nothing.
    """
    session_id = "soak-kill"
    path = _seed(session_id)
    held_marker = soak_home / "victim.held"

    victim = spawn(
        {
            "worker_id": "victim",
            "session_ids": [session_id],
            "hold_lock_seconds": _VICTIM_SECONDS,
            "held_path": str(held_marker),
            "result_path": str(soak_home / "victim.json"),
        },
    )
    _await_marker(held_marker, victim, "victim never acquired the session lock")

    assert victim.poll() is None, "victim exited before it could be killed holding the lock"
    os.kill(victim.pid, signal.SIGKILL)
    victim.wait(timeout=_PROCESS_TIMEOUT_SECONDS)
    assert victim.returncode == -signal.SIGKILL

    # The product-level claim: a writer that arrives after the holder died gets
    # the lock and its turns land. A stranded lock turns these into timeouts,
    # and the short timeout makes that a fast failure rather than a stall.
    size_before_survivor = path.stat().st_size
    survivor_result = soak_home / "survivor.json"
    _await(
        [
            spawn(
                {
                    "worker_id": "survivor",
                    "session_ids": [session_id],
                    "turns": 5,
                    "result_path": str(survivor_result),
                    "lock_timeout_seconds": _CONTENDED_TIMEOUT_SECONDS,
                },
            )
        ]
    )

    _metrics(soak_home).report("sigkill")
    assert path.stat().st_size > size_before_survivor, "the survivor wrote nothing after the kill"
    survivor = _results([survivor_result])[0]
    expected = {marker.split("/", 1)[1] for marker in survivor.written}
    assert expected <= _written_markers(_assert_readable(path)), (
        "the survivor's turns are missing: the dead holder's lock was not reclaimed"
    )


@pytest.mark.parametrize(
    "torn_tail",
    [
        pytest.param(
            b'{"id": "partial", "type": "custom_message", "text": "tor',
            id="partial-json",
        ),
        pytest.param(
            b'{"id": "partial", "type": "custom_message", "text": "\xe2\x82',
            id="partial-utf8-code-point",
        ),
    ],
)
def test_soak_write_after_a_torn_tail_is_not_swallowed(soak_home: Path, torn_tail: bytes) -> None:
    """Case 3, deterministic half: the record after a torn tail must survive.

    A SIGKILL lands mid-write only sometimes, so the crash *artifact* is seeded
    directly here — a final line with no newline is exactly what the killed
    process leaves behind. Losing the next turn is worse than the torn line
    itself: the torn line is one dead record a reader skips, while this silently
    swallows a good one written afterwards.
    """
    session_id = "soak-torn"
    path = _seed(session_id)
    JsonlSessionStore().append_turn(_session(session_id), "chat", "before-the-crash")
    before = json.loads(path.read_bytes().splitlines()[-1])
    with path.open("ab") as handle:
        handle.write(torn_tail)

    JsonlSessionStore().append_turn(_session(session_id), "chat", "after-the-crash")

    metrics = _metrics(soak_home)
    metrics.report("torn-tail")
    assert metrics.decode_failures >= 1

    raw_lines = path.read_bytes().splitlines()
    assert raw_lines[-2] == torn_tail
    after = json.loads(raw_lines[-1])
    assert after["text"] == "after-the-crash"
    assert after["parent_id"] == before["id"]

    restored = JsonlSessionRepo().load_session(session_id)
    assert restored is not None
    assert restored["entry_id"] == after["id"]
    assert restored["leaf_id"] == after["id"]
    assert [(item["kind"], item["text"]) for item in restored["history"]] == [
        ("chat", "before-the-crash"),
        ("chat", "after-the-crash"),
    ]


def test_soak_lock_timeout_fails_cleanly_without_partial_append(
    soak_home: Path, spawn: Callable[[dict[str, Any]], subprocess.Popen[bytes]]
) -> None:
    """Case 4: a write that cannot take the lock appends nothing at all.

    The write being *dropped* rather than retried or surfaced is #5475; this
    case pins the half that is not in dispute — whatever the caller is told, the
    file must not gain a partial record.
    """
    session_id = "soak-timeout"
    path = _seed(session_id)
    before = path.read_bytes()

    held = FileLock(f"{path}.lock", timeout=1.0)
    held.acquire()
    try:
        _await(
            [
                spawn(
                    {
                        "worker_id": "blocked",
                        "session_ids": [session_id],
                        "turns": 3,
                        "result_path": str(soak_home / "blocked.json"),
                        "lock_timeout_seconds": 0.3,
                    },
                )
            ]
        )
    finally:
        held.release()

    metrics = _metrics(soak_home)
    metrics.report("timeout")

    assert path.read_bytes() == before, "a timed-out write left bytes in the session file"
    assert metrics.timeouts >= 1, "expected the blocked writer to record a lock timeout"
