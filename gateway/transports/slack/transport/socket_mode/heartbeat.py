"""Liveness heartbeat for the Slack Socket Mode worker.

The gateway serves no HTTP port, so the container health check reads the mtime
of a heartbeat file instead of probing a socket. A background ticker refreshes
the file while the Socket Mode connection is live; if the connection drops or
the worker wedges, the file goes stale and the orchestrator restarts the task.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from config.constants.slack import SLACK_HEARTBEAT_STOP_TIMEOUT_SECONDS

DEFAULT_HEARTBEAT_PATH = "/workspace/scratch/gateway.heartbeat"

_logger = logging.getLogger(__name__)


class ConnectionHeartbeat:
    """Refreshes a heartbeat file while a liveness probe reports healthy.

    ``is_alive`` is checked on each tick (the Socket Mode client's
    ``is_connected``); the file is only touched while it returns true, so a
    dropped connection lets the file go stale. The ticker runs in the same
    process, so a fully wedged worker also stops refreshing it.
    """

    def __init__(
        self,
        *,
        path: str,
        is_alive: Callable[[], bool],
        interval_seconds: float = 15.0,
    ) -> None:
        self._path = Path(path)
        self._is_alive = is_alive
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def touch(self) -> None:
        """Update the heartbeat file's mtime, creating it and its parent if needed."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.touch()
        except OSError:
            _logger.debug("[slack-gateway] heartbeat touch failed", exc_info=True)

    def start(self) -> None:
        """Write an initial heartbeat and begin refreshing it on the interval."""
        # Clear the stop flag so a restart after stop() runs, not silently no-ops.
        self._stop.clear()
        self.touch()
        self._thread = threading.Thread(
            target=self._run,
            name="SlackGatewayHeartbeat",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            if self._is_alive():
                self.touch()

    def stop(self, *, timeout: float = SLACK_HEARTBEAT_STOP_TIMEOUT_SECONDS) -> None:
        """Stop the refresh ticker."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
