"""Characterization for the chat-transport registry in gateway.startup."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from gateway.core.process.shutdown_budget import ShutdownBudget
from gateway.transports import startup
from gateway.transports.names import TransportName
from gateway.transports.registration import TransportRegistration


class _Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_start_transports_skips_not_configured_and_failed(
    monkeypatch,
) -> None:
    """Not configured vs failed are distinct component statuses; others still start."""
    from gateway.core.lifecycle.errors import (
        GatewayConfigurationError,
        GatewayTransportFailedError,
    )

    workers = {
        TransportName.TELEGRAM: MagicMock(name="telegram-worker"),
        TransportName.SLACK: MagicMock(name="slack-worker"),
        TransportName.DISCORD: MagicMock(name="discord-worker"),
    }

    def _telegram(**_kwargs):
        raise GatewayConfigurationError("TELEGRAM_BOT_TOKEN is not set")

    def _slack(**_kwargs):
        return workers[TransportName.SLACK], MagicMock(name="slack-settings")

    def _discord(**_kwargs):
        raise GatewayTransportFailedError("startup timeout")

    monkeypatch.setattr(
        startup,
        "TRANSPORTS",
        (
            TransportRegistration(TransportName.TELEGRAM, _telegram, "polling for messages"),
            TransportRegistration(TransportName.SLACK, _slack, "connected via socket mode"),
            TransportRegistration(TransportName.DISCORD, _discord, "connected via gateway"),
        ),
    )

    started = startup.start_transports(
        logger=logging.getLogger("test.chat"),
        handler=MagicMock(),
    )

    assert [h.name for h in started.handles] == [TransportName.SLACK]
    assert started.handles[0].worker is workers[TransportName.SLACK]
    assert started.statuses[TransportName.TELEGRAM].startswith("not configured")
    assert started.statuses[TransportName.SLACK] == "connected via socket mode"
    assert started.statuses[TransportName.DISCORD].startswith("failed")


def test_stop_transports_stops_every_handle() -> None:
    w1 = MagicMock()
    w1.stop.return_value = True
    w2 = MagicMock()
    w2.stop.return_value = False
    handles = [
        startup.TransportHandle(TransportName.TELEGRAM, w1, "polling"),
        startup.TransportHandle(TransportName.SLACK, w2, "connected"),
    ]

    assert startup.stop_transports(handles=handles, timeout=1.5) is False
    assert w1.stop.call_args.kwargs["timeout"] == pytest.approx(1.5, abs=0.05)
    assert w2.stop.call_args.kwargs["timeout"] == pytest.approx(1.5, abs=0.05)


def test_stop_transports_subtracts_elapsed_from_later_workers(
    monkeypatch,
) -> None:
    """A stuck first worker must not get a fresh full timeout for the second."""
    clock = _Clock()

    def _budget(seconds: float) -> ShutdownBudget:
        return ShutdownBudget(seconds, clock=clock)

    monkeypatch.setattr("gateway.transports.startup.ShutdownBudget", _budget)
    w1 = MagicMock()
    w2 = MagicMock()

    def _slow_stop(*, timeout: float) -> bool:
        _ = timeout
        clock.now += 2.0
        return True

    w1.stop.side_effect = _slow_stop
    w2.stop.return_value = True
    handles = [
        startup.TransportHandle(TransportName.TELEGRAM, w1, "polling"),
        startup.TransportHandle(TransportName.SLACK, w2, "connected"),
    ]

    assert startup.stop_transports(handles=handles, timeout=8.0) is True
    w1.stop.assert_called_once_with(timeout=8.0)
    w2.stop.assert_called_once_with(timeout=6.0)
