"""Tests for REPL alert listener wiring in :mod:`surfaces.interactive_shell.controller`."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

from rich.console import Console

from config.repl_config import ReplConfig
from surfaces.interactive_shell.controller import _alert_listener


def test_alert_listener_replaces_stale_process_token(monkeypatch) -> None:
    os.environ["OPENSRE_ALERT_LISTENER_TOKEN"] = "stale"
    captured: list[str | None] = []

    def _fake_serve(*_args: object, **_kwargs: object) -> MagicMock:
        captured.append(os.environ.get("OPENSRE_ALERT_LISTENER_TOKEN"))
        handle = MagicMock()
        handle.bound_address = "127.0.0.1:8765"
        return handle

    monkeypatch.setattr(
        "infrastructure.asgi_server.serve_asgi_in_thread",
        _fake_serve,
    )
    cfg = ReplConfig(alert_listener_enabled=True, alert_listener_token="fresh")

    with _alert_listener(cfg, Console(force_terminal=False)) as inbox:
        assert inbox is not None
        assert captured == ["fresh"]

    assert os.environ.get("OPENSRE_ALERT_LISTENER_TOKEN") == "stale"


def test_alert_listener_clears_token_when_unconfigured(monkeypatch) -> None:
    os.environ["OPENSRE_ALERT_LISTENER_TOKEN"] = "stale"
    captured: list[str | None] = []

    def _fake_serve(*_args: object, **_kwargs: object) -> MagicMock:
        captured.append(os.environ.get("OPENSRE_ALERT_LISTENER_TOKEN"))
        handle = MagicMock()
        handle.bound_address = "127.0.0.1:8765"
        return handle

    monkeypatch.setattr(
        "infrastructure.asgi_server.serve_asgi_in_thread",
        _fake_serve,
    )
    cfg = ReplConfig(alert_listener_enabled=True, alert_listener_token=None)

    with _alert_listener(cfg, Console(force_terminal=False)) as inbox:
        assert inbox is not None
        assert captured == [None]

    assert os.environ.get("OPENSRE_ALERT_LISTENER_TOKEN") == "stale"
