"""Tests for the Telegram gateway daemon lifecycle helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gateway.core.process import component_status, supervision

_SLEEPER = (sys.executable, "-c", "import time; time.sleep(30)")
_CRASHER = (sys.executable, "-c", "print('boom'); raise SystemExit(1)")


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(supervision, "GATEWAY_PID_FILE", tmp_path / "gateway.pid")
    monkeypatch.setattr(supervision, "GATEWAY_LOG_FILE", tmp_path / "gateway.log")
    monkeypatch.setattr(component_status, "GATEWAY_COMPONENTS_FILE", tmp_path / "components.json")
    yield
    supervision.stop_gateway_daemon(timeout=5.0)


def test_pid_is_none_without_pidfile() -> None:
    assert supervision.gateway_daemon_pid() is None


def test_stale_pidfile_is_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    supervision.GATEWAY_PID_FILE.write_text("12345\n")
    monkeypatch.setattr(supervision, "process_is_alive", lambda _pid: False)

    assert supervision.gateway_daemon_pid() is None
    assert not supervision.GATEWAY_PID_FILE.exists()


def test_garbage_pidfile_is_ignored() -> None:
    supervision.GATEWAY_PID_FILE.write_text("not-a-pid\n")
    assert supervision.gateway_daemon_pid() is None


def test_start_stop_round_trip() -> None:
    ok, message = supervision.start_gateway_daemon(startup_wait=0.3, argv=_SLEEPER)
    assert ok, message
    pid = supervision.gateway_daemon_pid()
    assert pid is not None
    assert f"pid {pid}" in message

    ok, message = supervision.start_gateway_daemon(startup_wait=0.3, argv=_SLEEPER)
    assert ok
    assert "already running" in message

    ok, message = supervision.stop_gateway_daemon(timeout=5.0)
    assert ok, message
    assert supervision.gateway_daemon_pid() is None
    assert not supervision.GATEWAY_PID_FILE.exists()


def test_start_reports_startup_crash_with_log_tail() -> None:
    ok, message = supervision.start_gateway_daemon(startup_wait=5.0, argv=_CRASHER)
    assert not ok
    assert "exited during startup" in message
    assert "boom" in message
    assert supervision.gateway_daemon_pid() is None


def test_stop_escalates_to_sigkill_when_sigterm_is_ignored() -> None:
    stubborn = (
        sys.executable,
        "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
    )
    ok, message = supervision.start_gateway_daemon(startup_wait=0.5, argv=stubborn)
    assert ok, message

    ok, message = supervision.stop_gateway_daemon(timeout=1.0)
    assert ok, message
    assert "force-killed" in message
    assert supervision.gateway_daemon_pid() is None


def test_stop_when_not_running_is_ok() -> None:
    ok, message = supervision.stop_gateway_daemon()
    assert ok
    assert "not running" in message


def test_component_status_round_trip() -> None:
    components = {"web": "serving :8000", "telegram": "polling", "scheduler": "idle"}
    component_status.write_component_status(components)

    assert component_status.read_component_status() == components  # writer pid (ours) is alive

    component_status.clear_component_status()
    assert component_status.read_component_status() == {}


def test_component_status_ignored_when_process_is_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    component_status.write_component_status({"web": "serving :8000"})
    monkeypatch.setattr(component_status, "process_is_alive", lambda _pid: False)

    assert component_status.read_component_status() == {}


def test_log_tail_returns_last_lines() -> None:
    supervision.GATEWAY_LOG_FILE.write_text("".join(f"line-{i}\n" for i in range(100)))
    tail = supervision.read_gateway_log_tail(3)
    assert tail == "line-97\nline-98\nline-99"
    assert supervision.read_gateway_log_tail(0) == ""
