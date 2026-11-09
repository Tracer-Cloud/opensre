"""Shared background-thread lifecycle for poll-based gateway transports."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine
from typing import Any

from config.constants.gateway import DEFAULT_STOP_TIMEOUT_SECONDS


class PollingBackground:
    """Control handle for a poll transport's background thread."""

    def __init__(
        self,
        *,
        thread: threading.Thread,
        stop_event: threading.Event,
    ) -> None:
        self._thread = thread
        self._stop_event = stop_event

    def stop(self, *, timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS) -> bool:
        """Request shutdown and return whether the thread stopped."""
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def wait(self, *, timeout: float | None = None) -> bool:
        """Wait for the thread and return whether it has stopped."""
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()


def start_polling_background[RuntimeT](
    *,
    thread_name: str,
    started_message: str,
    fatal_message: str,
    logger: logging.Logger,
    initialize_runtime: Callable[[], RuntimeT],
    poll_until_stopped: Callable[[RuntimeT, threading.Event], Coroutine[Any, Any, None]],
    shutdown_runtime: Callable[[RuntimeT], None],
) -> PollingBackground:
    """Initialize, run, and shut down a poll transport on a background thread."""
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_run_polling_thread,
        kwargs={
            "stop_event": stop_event,
            "fatal_message": fatal_message,
            "logger": logger,
            "initialize_runtime": initialize_runtime,
            "poll_until_stopped": poll_until_stopped,
            "shutdown_runtime": shutdown_runtime,
        },
        name=thread_name,
        daemon=True,
    )
    thread.start()

    logger.info(started_message)
    return PollingBackground(thread=thread, stop_event=stop_event)


def _run_polling_thread[RuntimeT](
    *,
    stop_event: threading.Event,
    fatal_message: str,
    logger: logging.Logger,
    initialize_runtime: Callable[[], RuntimeT],
    poll_until_stopped: Callable[[RuntimeT, threading.Event], Coroutine[Any, Any, None]],
    shutdown_runtime: Callable[[RuntimeT], None],
) -> None:
    resources = initialize_runtime()
    try:
        asyncio.run(poll_until_stopped(resources, stop_event))
    except Exception:
        logger.critical(fatal_message, exc_info=True)
    finally:
        shutdown_runtime(resources)


__all__ = ["PollingBackground", "start_polling_background"]
