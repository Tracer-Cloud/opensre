"""Socket Mode worker stop shares SIGTERM with the heartbeat ticker."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from config.constants.slack import SLACK_HEARTBEAT_STOP_TIMEOUT_SECONDS
from gateway.core.process.shutdown_budget import ShutdownBudget
from gateway.transports.slack.transport.socket_mode.worker import SlackGatewayBackground


class _Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _JoinThread:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.join_timeout: float | None = None

    def start(self) -> None:
        return None

    def join(self, timeout: float | None = None) -> None:
        self.join_timeout = timeout

    def is_alive(self) -> bool:
        return False


def test_stop_subtracts_heartbeat_join_from_executor_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()

    def _budget(seconds: float) -> ShutdownBudget:
        return ShutdownBudget(seconds, clock=clock)

    monkeypatch.setattr(
        "gateway.transports.slack.transport.socket_mode.worker.ShutdownBudget",
        _budget,
    )
    waiter = _JoinThread()

    def _thread(*_args: Any, **_kwargs: Any) -> _JoinThread:
        return waiter

    monkeypatch.setattr(
        "gateway.transports.slack.transport.socket_mode.worker.threading.Thread",
        _thread,
    )
    heartbeat = MagicMock()

    def _heartbeat_stop(*, timeout: float) -> None:
        _ = timeout
        clock.now += 2.0

    heartbeat.stop.side_effect = _heartbeat_stop
    worker = SlackGatewayBackground(
        socket_client=MagicMock(),
        executor=MagicMock(),
        bindings=MagicMock(),
        heartbeat=heartbeat,
    )

    assert worker.stop(timeout=8.0) is True
    heartbeat.stop.assert_called_once_with(timeout=SLACK_HEARTBEAT_STOP_TIMEOUT_SECONDS)
    assert waiter.join_timeout == 6.0
