"""Local-host pub/sub bus for cross-agent findings over a Unix-domain socket.

Carries the same shape as ``core/state/models.py``'s ``evidence`` records so
findings published by one agent (claude-code, cursor, aider, ...) can later be
lifted into ``AgentState.evidence`` without re-mapping fields. See
``docs/platform/fleet.mdx`` for the on-the-wire schema.

Topology is a self-electing broker: the first ``publish`` or ``subscribe`` call
that finds no live socket binds it and runs an in-process daemon thread that
fans incoming JSONL messages out to every connected subscriber. Other processes
attach as plain clients. If the broker dies, the next operation re-elects.
"""

from __future__ import annotations

import atexit
import errno
import json
import logging
import os
import select
import socket
import threading
import types
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from config.constants import OPENSRE_HOME_DIR

_fcntl: types.ModuleType | None
try:
    import fcntl as _fcntl_impl
except ImportError:
    # ``fcntl`` is POSIX-only; PyInstaller Windows binaries must import this
    # module without failing. Cross-process broker election falls back to
    # bind/PID-file checks when ``flock`` is unavailable (see ``_ensure_broker``).
    _fcntl = None
else:
    _fcntl = _fcntl_impl

logger = logging.getLogger(__name__)

DEFAULT_BUS_SOCKET_PATH: Path = OPENSRE_HOME_DIR / "agents-bus.sock"

#: Bus message wire-format version. Bump when ``BusMessage`` fields change shape.
BUS_SCHEMA_VERSION: int = 1

#: Max bytes per JSONL frame on the wire. Frames over this are dropped with a
#: warning; a finding payload that big is almost certainly a bug.
_MAX_FRAME_BYTES: int = 64 * 1024

#: Per-subscriber write deadline used by ``BusServer._broadcast``. A subscriber
#: whose kernel recv buffer is full for longer than this is considered
#: unresponsive and evicted, so one wedged client cannot stall fan-out for
#: every other publisher's reader thread.
_BROADCAST_WRITE_TIMEOUT_SECONDS: float = 0.2


@dataclass(frozen=True)
class BusMessage:
    """A single finding published on the agent bus.

    Field shape mirrors ``AgentState.evidence`` entries so a message can be
    folded into agent state without renaming. ``agent`` follows the
    ``"<name>:<pid>"`` convention used by ``tools.system.fleet_monitoring.conflicts.WriteEvent``.

    ``data`` is wrapped in ``types.MappingProxyType`` at construction so the
    payload is read-only post-init; mutating ``msg.data["x"] = 1`` raises
    ``TypeError``. ``__hash__`` is explicitly disabled because ``data`` is a
    mapping and would otherwise produce a misleading auto-generated hash that
    fails at call time.
    """

    agent: str
    topic: str
    summary: str
    source: str = ""
    path: str = ""
    data: Mapping[str, object] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    schema_version: int = BUS_SCHEMA_VERSION

    # Disable hashing: a BusMessage carries a mapping and is not a value-key.
    __hash__ = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # Defensive copy + read-only view: protects against both external
        # mutation of the original dict and ``msg.data["x"] = 1`` after
        # construction. ``object.__setattr__`` bypasses the frozen check.
        object.__setattr__(self, "data", types.MappingProxyType(dict(self.data)))

    def to_jsonl(self) -> bytes:
        """Encode as a single newline-terminated JSON frame ready for the socket."""
        payload = {
            "agent": self.agent,
            "topic": self.topic,
            "summary": self.summary,
            "source": self.source,
            "path": self.path,
            "data": dict(self.data),
            "id": self.id,
            "timestamp": self.timestamp,
            "schema_version": self.schema_version,
        }
        return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")

    @classmethod
    def from_jsonl(cls, line: bytes | str) -> BusMessage:
        """Decode one JSONL frame into a ``BusMessage``. Raises on malformed input."""
        text = line.decode("utf-8") if isinstance(line, bytes) else line
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("bus frame must be a JSON object")
        return cls(
            agent=str(data["agent"]),
            topic=str(data["topic"]),
            summary=str(data["summary"]),
            source=str(data.get("source", "")),
            path=str(data.get("path", "")),
            data=dict(data.get("data", {})),
            id=str(data.get("id", uuid.uuid4())),
            timestamp=str(data.get("timestamp", datetime.now(UTC).isoformat())),
            schema_version=int(data.get("schema_version", BUS_SCHEMA_VERSION)),
        )


def _pid_file_for(socket_path: Path) -> Path:
    """Return the sidecar PID-file path for a given bus socket path."""
    return socket_path.with_name(socket_path.name + ".pid")


def _read_broker_pid(socket_path: Path) -> int | None:
    """Read the broker PID from the sidecar file, or ``None`` if missing/garbled."""
    pid_path = _pid_file_for(socket_path)
    try:
        text = pid_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _process_is_alive(pid: int) -> bool:
    """``os.kill(pid, 0)`` probe: True iff the PID maps to a live process we can signal."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it. Treat as alive — we still can't
        # safely unlink the socket out from under whoever owns it.
        return True
    except OSError:
        return False
    return True


def _socket_is_live(path: Path) -> bool:
    """Return True if a broker is currently listening on ``path``.

    Uses a PID-file side channel rather than connecting to the socket: the
    broker writes its PID on ``start()`` and removes it on ``stop()``. We treat
    the broker as live iff the socket file exists, the PID file exists, and
    the recorded PID maps to a process we can signal. This avoids creating a
    short-lived phantom subscriber + reader thread on every ``publish()`` /
    ``subscribe()`` call by a non-owner process.

    A stale PID file (broker crashed without cleanup) is reported as not-live;
    the caller's ``_unlink_stale`` path will remove the socket file and rebind.
    """
    if not path.exists():
        return False
    pid = _read_broker_pid(path)
    if pid is None:
        return False
    return _process_is_alive(pid)


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)


def _unlink_stale(path: Path) -> None:
    """Remove a socket file (and its sidecar PID file) that has no live listener."""
    with suppress(FileNotFoundError, OSError):
        os.unlink(path)
    with suppress(FileNotFoundError, OSError):
        os.unlink(_pid_file_for(path))


def _write_pid_file_atomic(path: Path, pid: int) -> None:
    """Write ``pid`` to the sidecar atomically (tmpfile + rename).

    Raises ``OSError`` on failure. Callers (i.e. ``BusServer.start``) must
    treat a missing PID file as a hard error: in multi-process operation,
    ``_socket_is_live`` reads the sidecar, and silently swallowing a write
    failure would let peers see the broker as dead, ``_unlink_stale`` its
    socket file out from under it, and silently split the bus.
    """
    pid_path = _pid_file_for(path)
    tmp = pid_path.with_name(pid_path.name + ".tmp")
    try:
        tmp.write_text(str(pid), encoding="utf-8")
        with suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, pid_path)
    except OSError:
        with suppress(FileNotFoundError, OSError):
            os.unlink(tmp)
        raise


class BusServer:
    """In-process broker that fans JSONL frames out to every connected subscriber.

    The first publisher or subscriber on a given socket path elects itself as
    broker by calling ``BusServer(path).start()``. The server runs an accept
    loop and per-connection reader threads as daemons, so the host process
    exits without needing to join them. Subscribers that disconnect or fail to
    receive are removed from the fan-out set on the next broadcast.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._listener: socket.socket | None = None
        # Map of subscriber socket -> per-connection write lock. Concurrent
        # broadcasts from multiple publisher reader-threads to the same
        # subscriber socket would otherwise interleave bytes mid-frame
        # (``sendall`` is multi-syscall for frames near the 64 KiB cap),
        # producing a garbled JSONL line the subscriber cannot parse. The
        # lock is per-subscriber so broadcasts to *different* subscribers
        # still proceed in parallel.
        self._subscribers: dict[socket.socket, threading.Lock] = {}
        self._lock = threading.Lock()
        self._running = threading.Event()
        self._accept_thread: threading.Thread | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        """Bind the socket, write the PID sidecar, and spawn the accept loop.

        Raises ``OSError`` on bind failure or on PID-file write failure (the
        sidecar is required for correct multi-process liveness; see
        ``_write_pid_file_atomic``). Any partial state is rolled back so a
        half-started broker never persists.
        """
        if self._running.is_set():
            return
        _ensure_parent_dir(self._path)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self._path))
        except OSError:
            listener.close()
            raise
        with suppress(OSError):
            os.chmod(self._path, 0o600)
        listener.listen(16)
        # Publish our PID via the sidecar so peers can answer "is the broker
        # live?" without making a real connection (which would otherwise spawn
        # a short-lived phantom subscriber on every probe). If this fails we
        # tear the bind down so a peer doesn't ``_unlink_stale`` our orphaned
        # socket file out from under us — ``_socket_is_live`` reads the
        # sidecar, and a missing one would silently split the bus.
        try:
            _write_pid_file_atomic(self._path, os.getpid())
        except OSError:
            with suppress(OSError):
                listener.shutdown(socket.SHUT_RDWR)
            with suppress(OSError):
                listener.close()
            with suppress(FileNotFoundError, OSError):
                os.unlink(self._path)
            raise
        self._listener = listener
        self._running.set()
        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name="agents-bus-accept",
            daemon=True,
        )
        self._accept_thread.start()

    def stop(self) -> None:
        """Shut the broker down: close the listener, drop all subscribers, unlink the socket."""
        if not self._running.is_set():
            return
        self._running.clear()
        listener, self._listener = self._listener, None
        if listener is not None:
            with suppress(OSError):
                listener.shutdown(socket.SHUT_RDWR)
            with suppress(OSError):
                listener.close()
        with self._lock:
            for sub in self._subscribers:
                with suppress(OSError):
                    sub.close()
            self._subscribers.clear()
        _unlink_stale(self._path)

    def _accept_loop(self) -> None:
        listener = self._listener
        if listener is None:
            return
        while self._running.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                # Listener closed during ``stop()`` — exit cleanly.
                return
            conn.setblocking(True)
            with self._lock:
                self._subscribers[conn] = threading.Lock()
            reader = threading.Thread(
                target=self._reader_loop,
                args=(conn,),
                name="agents-bus-reader",
                daemon=True,
            )
            reader.start()

    def _reader_loop(self, conn: socket.socket) -> None:
        """Read newline-delimited frames from one client and broadcast them."""
        buf = b""
        try:
            while self._running.is_set():
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
                if len(buf) > _MAX_FRAME_BYTES * 4:
                    logger.warning("bus client exceeded buffer cap; disconnecting")
                    return
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line:
                        continue
                    if len(line) > _MAX_FRAME_BYTES:
                        logger.warning("dropping oversized bus frame (%d bytes)", len(line))
                        continue
                    self._broadcast(line + b"\n", origin=conn)
        except OSError:
            return
        finally:
            self._drop_subscriber(conn)

    def _broadcast(self, frame: bytes, origin: socket.socket | None) -> None:
        with self._lock:
            # Snapshot (sub, write_lock) pairs so concurrent broadcasts to
            # different subscribers can proceed in parallel — only writes to
            # the *same* subscriber are serialized.
            targets = list(self._subscribers.items())
        dead: list[socket.socket] = []
        for sub, write_lock in targets:
            if sub is origin:
                # Don't echo a publisher's own frame back to itself.
                continue
            try:
                # Per-subscriber write lock prevents two publisher reader-
                # threads from interleaving bytes mid-frame on the same
                # socket (``sendall`` may issue multiple ``send`` syscalls
                # for large frames). Different subscribers have independent
                # locks, so cross-subscriber fan-out is unaffected.
                with write_lock:
                    # Write-readiness gate via ``select``: a blocking
                    # ``sendall`` on a subscriber whose kernel recv buffer is
                    # full would wedge the reader thread of *every*
                    # publisher, freezing fan-out across the bus. Using
                    # ``select`` instead of ``sub.settimeout`` so the
                    # per-connection ``_reader_loop``'s ``recv`` is
                    # unaffected (a quiet healthy subscriber must not be
                    # evicted).
                    _r, ready, _x = select.select([], [sub], [], _BROADCAST_WRITE_TIMEOUT_SECONDS)
                    if not ready:
                        logger.warning("bus subscriber unresponsive; evicting from fan-out")
                        dead.append(sub)
                        continue
                    sub.sendall(frame)
            except (OSError, ValueError):
                # ValueError: ``select`` rejects a closed fd (-1) by raising
                # ValueError rather than OSError. Treat it the same as a
                # broken socket — the subscriber is gone, drop it.
                dead.append(sub)
        for sub in dead:
            self._drop_subscriber(sub)

    def _drop_subscriber(self, conn: socket.socket) -> None:
        with self._lock:
            self._subscribers.pop(conn, None)
        with suppress(OSError):
            conn.close()


_broker_lock = threading.Lock()
_brokers: dict[Path, BusServer] = {}


_BIND_RACE_ERRNOS: frozenset[int] = frozenset({errno.EADDRINUSE, errno.EEXIST})


def _election_lock_path(socket_path: Path) -> Path:
    """Sidecar lock file used to serialize broker election across processes."""
    return socket_path.with_name(socket_path.name + ".lock")


def _acquire_election_flock(path: Path) -> int | None:
    """Open the election lock file and acquire an exclusive ``flock``.

    Returns the open fd on success, or ``None`` if the lock could not be
    obtained (file system without ``flock`` support, permission denied,
    Windows, ...). The caller is responsible for releasing + closing the fd via
    ``_release_election_flock``.
    """
    if _fcntl is None:
        return None
    lock_path = _election_lock_path(path)
    try:
        _ensure_parent_dir(lock_path)
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return None
    try:
        _fcntl.flock(fd, _fcntl.LOCK_EX)
    except OSError:
        with suppress(OSError):
            os.close(fd)
        return None
    return fd


def _release_election_flock(fd: int | None) -> None:
    if fd is None:
        return
    if _fcntl is not None:
        with suppress(OSError):
            _fcntl.flock(fd, _fcntl.LOCK_UN)
    with suppress(OSError):
        os.close(fd)


@contextmanager
def _hold_election_flock(path: Path) -> Iterator[None]:
    """Acquire the cross-process election flock for ``path`` for one ``with`` block.

    The fd lifecycle (``os.open`` → ``flock`` → ``flock LOCK_UN`` → ``os.close``)
    lives entirely in this scope so static analyzers can verify the file is
    always closed (CodeQL ``py/file-not-closed``). The standalone
    ``_acquire_election_flock`` / ``_release_election_flock`` helpers are kept
    for tests that exercise the half-paired primitive directly.
    """
    if _fcntl is None:
        # No flock support (Windows, exotic FS). Matches the
        # ``_acquire_election_flock`` → ``None`` contract: yield without
        # holding a cross-process lock.
        yield
        return

    lock_path = _election_lock_path(path)
    try:
        _ensure_parent_dir(lock_path)
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        yield
        return

    try:
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX)
        except OSError:
            # Could not acquire flock; proceed without it (best-effort
            # election, matching the original ``None``-on-failure contract).
            yield
            return
        try:
            yield
        finally:
            with suppress(OSError):
                _fcntl.flock(fd, _fcntl.LOCK_UN)
    finally:
        with suppress(OSError):
            os.close(fd)


def _ensure_broker(path: Path) -> BusServer | None:
    """Elect a broker for ``path`` if none is live, else return ``None``.

    Idempotent per-path: if this process already owns the broker, returns the
    existing instance. If another process owns it, returns ``None`` (the caller
    should connect as a client). If a stale socket file exists, unlinks it and
    retries the bind.

    Cross-process election is serialized by a POSIX ``flock`` on a sidecar
    lock file (``<socket>.lock``) when ``fcntl`` is available (not on Windows).
    Without ``flock``, two processes that both
    observe ``_socket_is_live`` → False can race through ``_unlink_stale`` +
    ``bind``: the kernel guarantees one bind succeeds, but the loser is left
    holding a listener fd whose filesystem path the winner just took, plus
    the accept/reader daemon threads it spawned — a real resource leak that
    persists for the loser's process lifetime. Where ``flock`` is available,
    holding it around the
    check-then-bind sequence makes election atomic across processes.

    A lost bind race (``EADDRINUSE`` / ``EEXIST``) is still converted to
    ``None`` defensively — flock is best-effort on exotic filesystems. Any
    other ``OSError`` from ``start()`` (e.g. PID-file write failure) is
    propagated — those are real errors users need to see, not bus splits to
    paper over silently.
    """
    # Fast in-process path: if we already own a running broker, no
    # cross-process work is needed.
    with _broker_lock:
        existing = _brokers.get(path)
        if existing is not None and existing.is_running:
            return existing

    with _hold_election_flock(path), _broker_lock:
        existing = _brokers.get(path)
        if existing is not None and existing.is_running:
            return existing
        if _socket_is_live(path):
            return None
        _unlink_stale(path)
        server = BusServer(path)
        try:
            server.start()
        except OSError as exc:
            if exc.errno in _BIND_RACE_ERRNOS:
                return None
            raise
        _brokers[path] = server
        return server


def _connect_client(path: Path, timeout: float) -> socket.socket:
    """Open a blocking UDS connection to the broker at ``path``."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path))
    except OSError:
        with suppress(OSError):
            client.close()
        raise
    client.settimeout(None)
    return client


@dataclass
class _CachedPublisher:
    """A persistent publisher connection plus the bookkeeping to share it safely.

    ``send_lock`` serializes ``sendall`` from concurrent publish() calls in the
    same process so frames don't interleave on the wire. ``drain_thread`` is a
    daemon that reads-and-discards anything the broker fans back to us — under
    multi-publisher load the broker would otherwise fill our kernel recv buffer
    with peers' frames, hit the write-timeout in ``_broadcast``, and evict our
    connection. Draining keeps the cached socket usable indefinitely.
    """

    sock: socket.socket
    send_lock: threading.Lock
    drain_thread: threading.Thread


_publisher_lock = threading.Lock()
_publishers: dict[Path, _CachedPublisher] = {}


def _drain_publisher_socket(sock: socket.socket) -> None:
    """Read-and-discard everything the broker sends to a cached publisher.

    Exits silently on EOF or socket error — at that point the cache entry
    will already have been (or is about to be) invalidated by the publish
    retry path.
    """
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                return
    except OSError:
        return


def _open_cached_publisher(path: Path, *, connect_timeout: float) -> _CachedPublisher:
    """Connect a fresh publisher and start its drain thread. Caller holds no lock."""
    sock = _connect_client(path, timeout=connect_timeout)
    cached = _CachedPublisher(
        sock=sock,
        send_lock=threading.Lock(),
        drain_thread=threading.Thread(
            target=_drain_publisher_socket,
            args=(sock,),
            name="agents-bus-publisher-drain",
            daemon=True,
        ),
    )
    cached.drain_thread.start()
    return cached


def _get_or_open_publisher(path: Path, *, connect_timeout: float) -> _CachedPublisher:
    """Return a cached publisher for ``path``, opening one if none exists."""
    with _publisher_lock:
        existing = _publishers.get(path)
        if existing is not None:
            return existing
    # Open outside the lock so concurrent first-publishers don't all serialize
    # behind a slow connect.
    fresh = _open_cached_publisher(path, connect_timeout=connect_timeout)
    with _publisher_lock:
        existing = _publishers.get(path)
        if existing is not None:
            # Lost the race; close ours and reuse theirs.
            with suppress(OSError):
                fresh.sock.close()
            return existing
        _publishers[path] = fresh
        return fresh


def _drop_publisher(path: Path, sock: socket.socket) -> None:
    """Remove the cached publisher for ``path`` if it still references ``sock``."""
    with _publisher_lock:
        cached = _publishers.get(path)
        if cached is not None and cached.sock is sock:
            del _publishers[path]
        else:
            cached = None
    if cached is not None:
        with suppress(OSError):
            cached.sock.close()


def _close_all_publishers() -> None:
    """Drop every cached publisher (e.g. at process exit). Safe to call repeatedly."""
    with _publisher_lock:
        sockets = [c.sock for c in _publishers.values()]
        _publishers.clear()
    for sock in sockets:
        with suppress(OSError):
            sock.close()


atexit.register(_close_all_publishers)


def publish(
    message: BusMessage,
    *,
    path: Path | None = None,
    connect_timeout: float = 1.0,
) -> None:
    """Publish ``message`` to every current subscriber on the bus.

    Self-elects a broker if none is running. Send is fire-and-forget: if no
    subscribers are attached, the frame is dropped by the broker (live-only,
    no replay buffer in v1).

    Publisher sockets are cached per ``path`` and reused across calls so a
    burst of publishes does not spawn one broker reader-thread per call. On
    any transient ``OSError`` — failed initial connect, broken cached
    connection, or send error — one retry is attempted (re-electing the
    broker if needed) before propagating the error.
    """
    target = path or DEFAULT_BUS_SOCKET_PATH
    _ensure_broker(target)
    frame = message.to_jsonl()
    last_err: OSError | None = None
    for attempt in range(2):
        cached: _CachedPublisher | None = None
        try:
            cached = _get_or_open_publisher(target, connect_timeout=connect_timeout)
            with cached.send_lock:
                cached.sock.sendall(frame)
            return
        except OSError as exc:
            last_err = exc
            if cached is not None:
                _drop_publisher(target, cached.sock)
            if attempt == 0:
                _ensure_broker(target)
    assert last_err is not None
    raise last_err


def subscribe(
    *,
    path: Path | None = None,
    connect_timeout: float = 1.0,
) -> Iterator[BusMessage]:
    """Yield ``BusMessage``s as they arrive on the bus until the broker disconnects.

    Self-elects a broker if none is running, then attaches as a subscriber and
    streams frames. Malformed lines are logged at WARNING and skipped — one
    misbehaving publisher should not kill an inspector REPL. The iterator ends
    cleanly on broker disconnect; ``KeyboardInterrupt`` propagates so callers
    (e.g. ``/fleet bus``) can return to their prompt.

    A buffer cap mirrors the broker's ``_reader_loop`` guard: any process that
    can ``bind()`` the socket first (filesystem perms are the only auth) could
    otherwise stream unlimited bytes without newlines and exhaust subscriber
    memory. On overflow the subscriber logs a warning and disconnects.

    Initial connect failures are retried once (mirroring ``publish()``) — the
    most common cause is a broker that just exited, in which case
    ``_ensure_broker`` will re-elect on the second pass.
    """
    target = path or DEFAULT_BUS_SOCKET_PATH
    last_connect_err: OSError | None = None
    client: socket.socket | None = None
    for _attempt in range(2):
        _ensure_broker(target)
        try:
            client = _connect_client(target, timeout=connect_timeout)
            break
        except OSError as exc:
            last_connect_err = exc
    if client is None:
        assert last_connect_err is not None
        raise last_connect_err
    buf = b""
    try:
        while True:
            try:
                chunk = client.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            if len(buf) > _MAX_FRAME_BYTES * 4:
                logger.warning(
                    "bus broker exceeded subscriber buffer cap (%d bytes); disconnecting",
                    len(buf),
                )
                return
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line:
                    continue
                if len(line) > _MAX_FRAME_BYTES:
                    logger.warning("dropping oversized bus frame (%d bytes)", len(line))
                    continue
                try:
                    yield BusMessage.from_jsonl(line)
                except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                    logger.warning("dropping malformed bus frame: %s", line[:80])
    finally:
        with suppress(OSError):
            client.close()


__all__ = [
    "BUS_SCHEMA_VERSION",
    "BusMessage",
    "BusServer",
    "DEFAULT_BUS_SOCKET_PATH",
    "publish",
    "subscribe",
]
