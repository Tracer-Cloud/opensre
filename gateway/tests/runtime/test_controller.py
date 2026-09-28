"""Tests for :mod:`gateway.core.lifecycle.controller` lifecycle behavior."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from config.constants.gateway import SCHEDULER_RELOAD_JOIN_TIMEOUT_SECONDS
from gateway.core.lifecycle.controller import GatewayController
from gateway.core.process.shutdown_budget import ShutdownBudget
from gateway.startup import StartedGateway
from gateway.transports.names import TransportName
from gateway.transports.startup import TransportHandle


class _Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_start_surfaces_delegates_to_the_startup_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manager stores an opaque StartedGateway; no per-transport fields."""
    telegram = TransportHandle(
        TransportName.TELEGRAM,
        MagicMock(name="telegram"),
        "polling for messages",
    )
    expected = StartedGateway(
        web_server=MagicMock(name="web"),
        transports={TransportName.TELEGRAM: telegram},
        statuses={"web": "serving", TransportName.TELEGRAM: "polling for messages"},
    )
    captured: dict[str, object] = {}

    def _boot(*, logger, handler, prompt_runner=None):
        captured["prompt_runner"] = prompt_runner
        captured["logger"] = logger
        captured["handler"] = handler
        return expected

    monkeypatch.setattr("gateway.core.lifecycle.controller.gateway_startup.start_gateway", _boot)
    manager = GatewayController()
    handler = MagicMock(name="chat-handler")
    logger = logging.getLogger("test.manager.surfaces")

    manager.start_surfaces(logger=logger, handler=handler)

    assert manager.surfaces is expected
    assert captured["logger"] is logger
    assert captured["handler"] is handler
    assert manager.components[TransportName.TELEGRAM] == "polling for messages"
    assert not hasattr(manager, "telegram_background_worker")
    assert not hasattr(manager, "web_server")


def test_wait_blocks_until_stop_not_channel_worker_exit() -> None:
    """The unified daemon should not exit when a chat worker thread ends."""
    manager = GatewayController()
    worker_wait = MagicMock(return_value=True)

    class FakeWorker:
        def wait(self, *, timeout: float | None = None) -> bool:
            return worker_wait(timeout=timeout)

        def stop(self, *, timeout: float = 8.0) -> bool:
            _ = timeout
            return True

    manager.surfaces = StartedGateway(
        transports={
            TransportName.TELEGRAM: TransportHandle(
                TransportName.TELEGRAM,
                FakeWorker(),
                "polling",
            )
        }
    )
    manager._stopped.clear()

    assert manager.wait(timeout=0.01) is False
    worker_wait.assert_not_called()

    manager.stop()
    assert manager.wait(timeout=0.01) is True
    assert manager.surfaces is None


def test_manager_stop_never_touches_the_real_gateway_directory() -> None:
    """Stopping a manager must not clear the running daemon's status file.

    ``stop()`` calls ``clear_component_status()``, which resolves a module
    global captured at import time. Without the isolation fixture in
    ``gateway/tests/conftest.py`` this test deleted
    ``~/.opensre/gateway/components.json`` on the developer's machine.
    """
    from gateway.core.process import component_status, supervision

    real_gateway_dir = Path.home() / ".opensre" / "gateway"

    GatewayController().stop()

    assert real_gateway_dir not in component_status.GATEWAY_COMPONENTS_FILE.parents
    assert real_gateway_dir not in supervision.GATEWAY_PID_FILE.parents


def test_manager_reload_scheduler_refreshes_component_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loop/cron mutations must resync the long-lived gateway scheduler."""
    publishes: list[dict[str, str]] = []

    def _refresh(scheduler: object | None, runners: object, *, task_filter=None):
        _ = (task_filter, runners)
        assert scheduler is None
        return object(), 3

    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.runner.refresh_background_scheduler",
        _refresh,
    )
    manager = GatewayController()
    monkeypatch.setattr(
        manager,
        "_publish_status",
        lambda _logger: publishes.append(dict(manager.components)),
    )

    manager._reload_scheduler(logging.getLogger("test"))

    assert manager.components["scheduler"] == "running 3 scheduled task(s)"
    assert publishes == [{"scheduler": "running 3 scheduled task(s)"}]


def test_stop_subtracts_reload_join_from_the_surface_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reload-watcher join is a slice of SIGTERM, not extra time on top of it."""
    clock = _Clock()

    def _budget(seconds: float) -> ShutdownBudget:
        return ShutdownBudget(seconds, clock=clock)

    monkeypatch.setattr("gateway.core.lifecycle.controller.ShutdownBudget", _budget)
    manager = GatewayController()
    reload_thread = MagicMock()

    def _join(timeout: float | None = None) -> None:
        _ = timeout
        clock.now += 2.0

    reload_thread.join.side_effect = _join
    manager._scheduler_reload_thread = reload_thread
    surfaces = MagicMock()
    surfaces.stop.return_value = True
    manager.surfaces = surfaces

    assert manager.stop(timeout=8.0) is True
    reload_thread.join.assert_called_once_with(timeout=SCHEDULER_RELOAD_JOIN_TIMEOUT_SECONDS)
    surfaces.stop.assert_called_once_with(timeout=6.0)
    assert manager.surfaces is None
    assert manager._scheduler_reload_thread is None
