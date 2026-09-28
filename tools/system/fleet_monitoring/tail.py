"""Live stdout tail for the monitor-local-agents fleet view.

Backs the ``/fleet trace <pid>`` slash command. The attach path only
accepts *regular files* backing fd 1 of the target pid: TTY/PTY/pipe/
socket/anon_inode targets are rejected at attach time with a precise
error so we never compete with the legitimate consumer for bytes.
PTY interception for OpenSRE-spawned agents is left to a future change
once a spawn path lands.

Layered bounding (per the 4 MiB acceptance criterion):
    * the reader thread publishes into a ``queue.Queue`` with a fixed
      ``maxsize``; on overflow the oldest chunk is dropped so the
      stream stays fresh under burst writers
    * :class:`TailBuffer` caps the *consumer-side* accumulator at
      ``DEFAULT_MAX_BYTES`` and drops whole chunks from the head so the
      UTF-8 decode boundary in :meth:`TailBuffer.decoded` always sits
      between chunks (no mid-codepoint splits)
"""

from __future__ import annotations

import contextlib
import os
import queue
import stat
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tools.system.fleet_monitoring.probe import pid_exists

DEFAULT_MAX_BYTES: Final[int] = 4 * 1024 * 1024
DEFAULT_QUEUE_MAX: Final[int] = 128
DEFAULT_POLL_INTERVAL_S: Final[float] = 0.1
DEFAULT_READ_BUFFER: Final[int] = 4096

_LSOF_TIMEOUT_S: Final[float] = 5.0
_THREAD_JOIN_TIMEOUT_S: Final[float] = 1.0
_SENTINEL: Final[object] = object()


class AttachUnsupportedError(Exception):
    """Raised eagerly by :func:`attach` when the target cannot be tailed.

    ``reason`` is a short, user-facing message that the slash-command
    handler renders directly. It must not contain markup or ANSI codes.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class _ResolvedTarget:
    """Regular-file target backing fd 1 of a tracked pid."""

    pid: int
    path: Path


def _check_regular_file(path: Path, *, what: str) -> Path:
    try:
        st = path.stat()
    except FileNotFoundError as exc:
        raise AttachUnsupportedError(f"{what} target {path} no longer exists") from exc
    except PermissionError as exc:
        raise AttachUnsupportedError(
            f"{what} target {path} is not readable (permission denied)"
        ) from exc
    except OSError as exc:
        raise AttachUnsupportedError(
            f"{what} target {path} is unreachable: {exc.strerror or exc}"
        ) from exc
    if not stat.S_ISREG(st.st_mode):
        raise AttachUnsupportedError(
            f"{what} is not a regular file (got {stat.filemode(st.st_mode)})"
        )
    return path


def _resolve_linux_target(pid: int) -> _ResolvedTarget:
    fd_link = Path(f"/proc/{pid}/fd/1")
    try:
        target = os.readlink(fd_link)
    except FileNotFoundError as exc:
        raise AttachUnsupportedError(f"no such pid {pid}") from exc
    except PermissionError as exc:
        raise AttachUnsupportedError(f"cannot inspect pid {pid} (permission denied)") from exc
    except OSError as exc:
        raise AttachUnsupportedError(f"cannot inspect pid {pid}: {exc.strerror or exc}") from exc

    if target.startswith("socket:["):
        raise AttachUnsupportedError("stdout is a socket; live tail not supported")
    if target.startswith("pipe:["):
        raise AttachUnsupportedError("stdout is a pipe; live tail not supported")
    if target.startswith("anon_inode:"):
        raise AttachUnsupportedError(f"stdout is {target}; live tail not supported")
    if not target.startswith("/"):
        raise AttachUnsupportedError(f"stdout target {target!r} is not a filesystem path")
    if target.startswith(("/dev/pts/", "/dev/tty")):
        raise AttachUnsupportedError("stdout is on a terminal; live tail not supported")
    if target == "/dev/null":
        raise AttachUnsupportedError("stdout is /dev/null; nothing to tail")

    return _ResolvedTarget(pid=pid, path=_check_regular_file(Path(target), what="stdout"))


def _parse_lsof_fd1(stdout: str) -> tuple[str | None, str | None]:
    """Return ``(type, name)`` for the ``f1`` block in ``lsof -F ftn`` output.

    Each fd block starts with ``f<num>`` and is followed by zero or more
    field lines of the form ``<letter><value>`` (here: ``t`` for fd type,
    ``n`` for fd name). A new ``f`` line ends the previous block. Fields
    we don't care about are silently skipped.
    """
    fd: str | None = None
    fd_type: str | None = None
    fd_name: str | None = None
    captured = False

    for line in stdout.splitlines():
        if not line:
            continue
        prefix, value = line[0], line[1:]
        if prefix == "f":
            if fd == "1":
                captured = True
                break
            fd = value
            fd_type = None
            fd_name = None
        elif fd == "1":
            if prefix == "t":
                fd_type = value
            elif prefix == "n":
                fd_name = value

    if fd == "1" and not captured:
        captured = True

    if not captured:
        return None, None
    return fd_type, fd_name


def _resolve_macos_target(pid: int) -> _ResolvedTarget:
    try:
        proc = subprocess.run(
            ["lsof", "-F", "ftn", "-p", str(pid)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_LSOF_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AttachUnsupportedError("lsof not found; cannot resolve stdout on this host") from exc
    except subprocess.TimeoutExpired as exc:
        raise AttachUnsupportedError(f"lsof timed out resolving pid {pid}") from exc

    if proc.returncode != 0 and not proc.stdout:
        detail = proc.stderr.strip() or "unknown error"
        raise AttachUnsupportedError(f"no such pid {pid} (lsof: {detail})")

    fd_type, fd_name = _parse_lsof_fd1(proc.stdout)
    if fd_type is None and fd_name is None:
        raise AttachUnsupportedError(f"pid {pid} has no fd 1 (lsof returned no stdout entry)")
    if fd_type is None:
        raise AttachUnsupportedError("lsof returned no type for stdout")
    if fd_type != "REG":
        kind = fd_type.upper()
        # ``/dev/null`` and ``/dev/zero`` come back as CHR with their path in
        # ``n``; surface a tail-specific message rather than the generic
        # "stdout is on a terminal" reject so the user understands the cause.
        if fd_name == "/dev/null":
            raise AttachUnsupportedError("stdout is /dev/null; nothing to tail")
        if kind == "CHR":
            raise AttachUnsupportedError("stdout is on a terminal; live tail not supported")
        if kind == "PIPE":
            raise AttachUnsupportedError("stdout is a pipe; live tail not supported")
        if kind in {"IPV4", "IPV6", "UNIX"}:
            raise AttachUnsupportedError("stdout is a socket; live tail not supported")
        raise AttachUnsupportedError(f"stdout fd type {kind} is not a regular file")
    if not fd_name:
        raise AttachUnsupportedError("lsof returned no name for stdout")

    return _ResolvedTarget(pid=pid, path=_check_regular_file(Path(fd_name), what="stdout"))


def _resolve_target(pid: int) -> _ResolvedTarget:
    if sys.platform == "win32":
        raise AttachUnsupportedError("Windows is not supported")
    # Guard non-positive ids before probing: ``psutil.pid_exists(0)`` can
    # raise ``PermissionError`` on macOS, which the slash-command handler
    # doesn't catch (it only catches :class:`AttachUnsupportedError`).
    if pid <= 0:
        raise AttachUnsupportedError(f"invalid pid {pid} (must be positive)")
    if not pid_exists(pid):
        raise AttachUnsupportedError(f"no such pid {pid}")
    if sys.platform == "darwin":
        return _resolve_macos_target(pid)
    if sys.platform.startswith("linux"):
        return _resolve_linux_target(pid)
    raise AttachUnsupportedError(f"platform {sys.platform!r} is not supported")


class TailBuffer:
    """Bounded byte ring; bound is total bytes across whole chunks.

    Drops *whole chunks* from the head on overflow. The cap is a soft
    upper bound: the buffer only drops *after* an append exceeds it,
    so the actual peak briefly exceeds the cap by at most the size of
    the appending chunk. We never split a chunk, so the UTF-8 decode
    boundary in :meth:`decoded` always sits between chunks.
    """

    def __init__(self, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._max_bytes = max_bytes
        self._chunks: deque[bytes] = deque()
        self._size = 0

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._chunks.append(chunk)
        self._size += len(chunk)
        # Always retain at least one chunk so a single oversized append
        # is still reachable (the alternative would silently drop a
        # message bigger than the cap).
        while self._size > self._max_bytes and len(self._chunks) > 1:
            head = self._chunks.popleft()
            self._size -= len(head)

    def snapshot(self) -> bytes:
        return b"".join(self._chunks)

    def decoded(self) -> str:
        return self.snapshot().decode("utf-8", errors="replace")

    def __len__(self) -> int:
        return self._size


class AttachSession:
    """Iterable + context manager owning the reader thread, fd, and queue.

    Construct via :func:`attach` (never directly). The constructor
    opens the file at the resolved target, seeks to EOF, and starts a
    daemon reader thread. ``__iter__`` drains a bounded queue until the
    reader posts a sentinel (process exited, file vanished, or
    :meth:`close` was called).
    """

    def __init__(
        self,
        target: _ResolvedTarget,
        *,
        buffer_bytes: int = DEFAULT_MAX_BYTES,
        queue_max: int = DEFAULT_QUEUE_MAX,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        read_buffer: int = DEFAULT_READ_BUFFER,
    ) -> None:
        self.target = target
        self.buffer = TailBuffer(buffer_bytes)
        # Set by :meth:`_reader_loop` when it exits because ``pid_exists``
        # went False — i.e. the producer died. Stays False on
        # user-initiated close (``stop_event``) or fd error, which lets
        # the slash-command UI distinguish "process exited" from "you
        # pressed Ctrl+C" in the trailer.
        self.producer_exited = False
        self._queue: queue.Queue[bytes | object] = queue.Queue(maxsize=queue_max)
        self._stop_event = threading.Event()
        self._poll_interval_s = poll_interval_s
        self._read_buffer = read_buffer
        self._closed = False

        # Open + seek before spawning the thread so any OSError surfaces
        # at the call site, not inside the iterator.
        # SIM115 suppressed: the file handle is stored on self and kept open
        # for the lifetime of the background reader thread (_reader_loop).
        # A with-block would close it immediately on __init__ exit, before
        # the thread has read anything. The fd is closed in close() / __del__.
        try:
            self._fd = open(target.path, "rb", buffering=0)  # noqa: SIM115
        except OSError as exc:
            raise AttachUnsupportedError(
                f"cannot open {target.path}: {exc.strerror or exc}"
            ) from exc
        try:
            self._fd.seek(0, os.SEEK_END)
        except OSError as exc:
            self._fd.close()
            raise AttachUnsupportedError(
                f"cannot seek to EOF on {target.path}: {exc.strerror or exc}"
            ) from exc

        self._thread = threading.Thread(
            target=self._reader_loop,
            name=f"agents-tail-{target.pid}",
            daemon=True,
        )
        self._thread.start()

    def _reader_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    chunk = self._fd.read(self._read_buffer)
                except OSError:
                    break
                if chunk:
                    self._publish(chunk)
                    continue
                # EOF for now while the PID is still alive: we poll below until
                # new bytes arrive or the process exits. A quiet writer leaves
                # the rendered view unchanged; see trace limitations in
                # docs/platform/fleet.mdx.
                #
                # The only *exit* trigger here is PID death — we deliberately
                # do NOT check ``self.target.path.exists()`` once the fd is
                # open so we keep following the inode through rename/unlink
                # (logrotate semantics), same as ``tail -f``. An exists-check
                # would silently end the trace mid-incident while the agent
                # still writes the original inode.
                if not pid_exists(self.target.pid):
                    self.producer_exited = True
                    break
                self._stop_event.wait(self._poll_interval_s)
        finally:
            # The sentinel is the only signal the consumer receives; if
            # it never lands the iterator hangs on ``queue.get``. This
            # post is also single-producer (the loop above has exited
            # and ``_publish`` is no longer reachable), so the
            # non-atomic drop-oldest dance below is safe — see the
            # ``_publish`` comment for the invariant a future refactor
            # has to preserve.
            try:
                self._queue.put_nowait(_SENTINEL)
            except queue.Full:
                # Queue is full of pending data chunks. Drop the head
                # (oldest data) to make room — the consumer will see
                # the sentinel as soon as it drains down to it.
                with contextlib.suppress(queue.Empty):
                    self._queue.get_nowait()
                with contextlib.suppress(queue.Full):
                    self._queue.put_nowait(_SENTINEL)

    def _publish(self, chunk: bytes) -> None:
        # **Single-producer invariant.** Only :meth:`_reader_loop` (one
        # thread per session) calls this. The ``get_nowait`` + ``put_nowait``
        # pair below is *not* atomic — with a second producer it would
        # become a real race: the second producer could fill the slot we
        # just freed, and the next ``put_nowait`` here would either drop
        # *its* chunk instead of the oldest, or get another ``Full`` and
        # silently lose the current chunk. If a future change adds
        # another producer (e.g. multiplexing stderr alongside stdout),
        # serialize this whole block with a ``threading.Lock`` first.
        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            # Drop-oldest to keep the stream fresh under burst writers;
            # the TailBuffer cap on the consumer side preserves the
            # acceptance memory bound regardless.
            with contextlib.suppress(queue.Empty):
                self._queue.get_nowait()
            with contextlib.suppress(queue.Full):
                self._queue.put_nowait(chunk)

    def __iter__(self) -> Iterator[bytes]:
        return self

    def __next__(self) -> bytes:
        while True:
            try:
                item = self._queue.get(timeout=self._poll_interval_s)
            except queue.Empty:
                if not self._thread.is_alive():
                    raise StopIteration from None
                continue
            if item is _SENTINEL:
                raise StopIteration
            assert isinstance(item, bytes)
            # Append-on-yield keeps :attr:`buffer` in lockstep with what
            # the consumer sees, so the slash-command renderer only has
            # to call ``sess.buffer.decoded()`` — no risk of forgetting
            # to feed the buffer and silently OOM'ing the live view.
            self.buffer.append(item)
            return item

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        self._thread.join(timeout=_THREAD_JOIN_TIMEOUT_S)
        # Closing the fd while the reader is still inside ``read()`` is
        # undefined behavior with buffered IO. With ``buffering=0`` it's
        # tolerated (the read returns EBADF and the thread exits via
        # ``except OSError``), but on the off-chance the reader is stuck
        # on a slow/stalled FS we'd rather leak the fd than risk a
        # crash — the daemon thread is reaped at process exit.
        if not self._thread.is_alive():
            with contextlib.suppress(OSError):
                self._fd.close()

    def __enter__(self) -> AttachSession:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


def attach(
    pid: int,
    *,
    buffer_bytes: int = DEFAULT_MAX_BYTES,
    queue_max: int = DEFAULT_QUEUE_MAX,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    read_buffer: int = DEFAULT_READ_BUFFER,
) -> AttachSession:
    """Validate the target eagerly and return a ready-to-iterate session.

    Raises :class:`AttachUnsupportedError` synchronously on any state we
    cannot tail-from-EOF safely (Windows, missing pid, fd is a TTY/
    PTY/pipe/socket/anon_inode/``/dev/null``, permission denied, file
    vanished, open failed). Caller is responsible for closing the
    session — preferably via ``with attach(pid) as sess: …``.
    """
    target = _resolve_target(pid)
    return AttachSession(
        target,
        buffer_bytes=buffer_bytes,
        queue_max=queue_max,
        poll_interval_s=poll_interval_s,
        read_buffer=read_buffer,
    )


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_QUEUE_MAX",
    "DEFAULT_READ_BUFFER",
    "AttachSession",
    "AttachUnsupportedError",
    "TailBuffer",
    "attach",
]
