"""Bounded command capture retaining a shared stdout/stderr head and tail."""

from __future__ import annotations

import threading
from collections import deque
from typing import Literal

from config.constants.tool_output import TOOL_OUTPUT_CAPTURE_MAX_BYTES

type OutputStream = Literal["stdout", "stderr"]


class ShellOutputCapture:
    """Retain at most 1 MiB across both streams while continuing to drain them."""

    def __init__(self) -> None:
        self._head_budget = TOOL_OUTPUT_CAPTURE_MAX_BYTES // 2
        self._tail_budget = TOOL_OUTPUT_CAPTURE_MAX_BYTES - self._head_budget
        self._head: list[tuple[OutputStream, bytes]] = []
        self._head_bytes = 0
        self._tail: deque[tuple[OutputStream, bytes]] = deque()
        self._tail_bytes = 0
        self._omitted: dict[OutputStream, int] = {"stdout": 0, "stderr": 0}
        self._lock = threading.Lock()

    def append(self, stream: OutputStream, text: str) -> None:
        """Append a chunk, discarding only the oldest bytes outside the stable head."""
        encoded = text.encode("utf-8")
        with self._lock:
            head_size = min(len(encoded), self._head_budget - self._head_bytes)
            if head_size:
                self._head.append((stream, encoded[:head_size]))
                self._head_bytes += head_size
            rest = encoded[head_size:]
            if rest:
                self._tail.append((stream, rest))
                self._tail_bytes += len(rest)
            while self._tail_bytes > self._tail_budget:
                removed_stream, chunk = self._tail.popleft()
                removed = min(len(chunk), self._tail_bytes - self._tail_budget)
                self._omitted[removed_stream] += removed
                self._tail_bytes -= removed
                if removed < len(chunk):
                    self._tail.appendleft((removed_stream, chunk[removed:]))

    def snapshot(self) -> tuple[str, str, str, bool]:
        """Return stdout, stderr, combined output, and whether capture discarded bytes."""
        with self._lock:
            head = list(self._head)
            tail = list(self._tail)
            omitted = dict(self._omitted)
        stdout = self._render(head, tail, omitted["stdout"], stream="stdout")
        stderr = self._render(head, tail, omitted["stderr"], stream="stderr")
        # Stable stream order keeps stderr diagnostics independent of reader scheduling.
        combined = stdout + stderr
        return stdout, stderr, combined, any(omitted.values())

    @staticmethod
    def _render(
        head: list[tuple[OutputStream, bytes]],
        tail: list[tuple[OutputStream, bytes]],
        omitted: int,
        *,
        stream: OutputStream,
    ) -> str:
        prefix = b"".join(chunk for name, chunk in head if name == stream)
        suffix = b"".join(chunk for name, chunk in tail if name == stream)
        if not omitted:
            return (prefix + suffix).decode("utf-8", errors="replace")
        return (
            prefix.decode("utf-8", errors="ignore")
            + f"\n... {omitted} bytes omitted ...\n"
            + suffix.decode("utf-8", errors="ignore")
        )
