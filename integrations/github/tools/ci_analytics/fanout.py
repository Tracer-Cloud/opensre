"""Bounded fan-out of blocking GitHub requests whose results are handled on the caller's thread."""

from __future__ import annotations

import queue
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, TypeVar

_T = TypeVar("_T")


class RequestFanout:
    """Runs request calls on one bounded pool; each completion is handled on the thread in ``run``.

    A handler may submit further calls, so a whole dependency graph of
    requests shares one concurrency bound without a worker ever waiting on
    another (no pool starvation) and without locks around the handlers'
    state; ``submit`` is therefore only called before ``run`` or from a
    handler. The first error a handler raises (``future.result()`` re-raises
    a failed call) ends ``run``: calls still queued are cancelled and the
    error propagates at once, without waiting for requests already on the
    wire.
    """

    def __init__(self, *, workers: int, thread_name_prefix: str) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=thread_name_prefix)
        self._handlers: dict[Future[Any], Callable[[Future[Any]], None]] = {}
        self._finished: queue.SimpleQueue[Future[Any]] = queue.SimpleQueue()

    def submit(self, call: Callable[[], _T], handle: Callable[[Future[_T]], None]) -> None:
        """Queue ``call``; ``handle`` receives its finished future on the ``run`` thread."""
        future = self._pool.submit(call)
        self._handlers[future] = handle
        future.add_done_callback(self._finished.put)

    def run(self) -> None:
        """Handle completions until no call is pending; the pool cannot be reused afterwards."""
        try:
            while self._handlers:
                future = self._finished.get()
                self._handlers.pop(future)(future)
        finally:
            self._pool.shutdown(wait=False, cancel_futures=True)


__all__ = ["RequestFanout"]
