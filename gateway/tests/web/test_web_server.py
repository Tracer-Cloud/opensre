"""Live round-trip test for the shared in-thread web server."""

from __future__ import annotations

import json
import sys
import types
import urllib.request

import pytest
import uvicorn

from core.domain.alerts.inbox import AlertInbox, set_current_inbox
from gateway.web.web_server import serve_webapp_foreground, serve_webapp_in_thread


def test_serve_stop_round_trip_on_ephemeral_port() -> None:
    inbox = AlertInbox()
    set_current_inbox(inbox)
    handle = serve_webapp_in_thread(host="127.0.0.1", port=0)
    try:
        assert handle.bound_port > 0
        base = f"http://{handle.bound_address}"

        with urllib.request.urlopen(f"{base}/healthz", timeout=5) as resp:
            assert resp.status == 200

        request = urllib.request.Request(
            f"{base}/alerts",
            data=json.dumps({"text": "disk full"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            assert resp.status == 202

        queued = inbox.pop_nowait()
        assert queued is not None
        assert queued.text == "disk full"
    finally:
        handle.stop()
        set_current_inbox(None)

    assert not handle.thread.is_alive()


def test_foreground_server_binds_every_interface_on_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "8123")
    app = object()
    webapp = types.ModuleType("gateway.web.webapp")
    webapp.app = app  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gateway.web.webapp", webapp)
    captured: dict[str, object] = {}

    def _run(application: object, *, host: str, port: int) -> None:
        captured["app"] = application
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr(uvicorn, "run", _run)

    serve_webapp_foreground()

    assert captured == {"app": app, "host": "0.0.0.0", "port": 8123}
