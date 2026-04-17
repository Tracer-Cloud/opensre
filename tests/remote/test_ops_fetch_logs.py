"""Tests for RailwayRemoteOpsProvider.fetch_logs()."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.remote.ops import RailwayRemoteOpsProvider, RemoteOpsError, RemoteServiceScope


class _Result:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_run(log_output: str = "", log_returncode: int = 0, log_stderr: str = ""):
    def _run(cmd, check, text, capture_output):  # noqa: ANN001
        _ = (check, text, capture_output)
        if cmd[:2] == ["railway", "link"]:
            return _Result(0, stdout="{}")
        if cmd[:2] == ["railway", "logs"]:
            return _Result(log_returncode, stdout=log_output, stderr=log_stderr)
        return _Result(1, stderr="unexpected command")

    return _run


def test_fetch_logs_returns_captured_output() -> None:
    provider = RailwayRemoteOpsProvider()
    scope = RemoteServiceScope(provider="railway", project="proj", service="svc")

    log_text = "2026-04-17 10:00:00 INFO app started\n2026-04-17 10:01:00 ERROR db timeout"

    with (
        patch("app.remote.ops.shutil.which", return_value="/usr/local/bin/railway"),
        patch("app.remote.ops.subprocess.run", side_effect=_make_run(log_output=log_text)),
    ):
        result = provider.fetch_logs(scope, lines=50)

    assert result == log_text


def test_fetch_logs_strips_whitespace() -> None:
    provider = RailwayRemoteOpsProvider()
    scope = RemoteServiceScope(provider="railway", project="proj", service="svc")

    with (
        patch("app.remote.ops.shutil.which", return_value="/usr/local/bin/railway"),
        patch(
            "app.remote.ops.subprocess.run",
            side_effect=_make_run(log_output="\n\n  log content  \n\n"),
        ),
    ):
        result = provider.fetch_logs(scope, lines=10)

    assert result == "log content"


def test_fetch_logs_returns_empty_string_when_no_output() -> None:
    provider = RailwayRemoteOpsProvider()
    scope = RemoteServiceScope(provider="railway", project="proj", service="svc")

    with (
        patch("app.remote.ops.shutil.which", return_value="/usr/local/bin/railway"),
        patch("app.remote.ops.subprocess.run", side_effect=_make_run(log_output="")),
    ):
        result = provider.fetch_logs(scope, lines=10)

    assert result == ""


def test_fetch_logs_raises_when_railway_cli_missing() -> None:
    provider = RailwayRemoteOpsProvider()
    scope = RemoteServiceScope(provider="railway")

    with (
        patch("app.remote.ops.shutil.which", return_value=None),
        pytest.raises(RemoteOpsError, match="Railway CLI is not installed"),
    ):
        provider.fetch_logs(scope, lines=10)


def test_fetch_logs_raises_on_non_zero_exit() -> None:
    provider = RailwayRemoteOpsProvider()
    scope = RemoteServiceScope(provider="railway", project="proj", service="svc")

    with (
        patch("app.remote.ops.shutil.which", return_value="/usr/local/bin/railway"),
        patch(
            "app.remote.ops.subprocess.run",
            side_effect=_make_run(log_returncode=1, log_stderr="not linked"),
        ),
        pytest.raises(RemoteOpsError, match="Railway command failed"),
    ):
        provider.fetch_logs(scope, lines=10)


def test_fetch_logs_appends_stderr_when_both_present() -> None:
    provider = RailwayRemoteOpsProvider()
    scope = RemoteServiceScope(provider="railway", project="proj", service="svc")

    with (
        patch("app.remote.ops.shutil.which", return_value="/usr/local/bin/railway"),
        patch(
            "app.remote.ops.subprocess.run",
            side_effect=_make_run(log_output="main log line", log_stderr="advisory message"),
        ),
    ):
        result = provider.fetch_logs(scope, lines=10)

    assert "main log line" in result
    assert "[stderr: advisory message]" in result


def test_fetch_logs_returns_stderr_when_stdout_empty() -> None:
    provider = RailwayRemoteOpsProvider()
    scope = RemoteServiceScope(provider="railway", project="proj", service="svc")

    with (
        patch("app.remote.ops.shutil.which", return_value="/usr/local/bin/railway"),
        patch(
            "app.remote.ops.subprocess.run",
            side_effect=_make_run(log_output="", log_stderr="warning: logs delayed"),
        ),
    ):
        result = provider.fetch_logs(scope, lines=10)

    assert result == "warning: logs delayed"


def test_fetch_logs_passes_tail_argument() -> None:
    provider = RailwayRemoteOpsProvider()
    scope = RemoteServiceScope(provider="railway", project="proj", service="svc")

    captured: list[list[str]] = []

    def _run(cmd, check, text, capture_output):  # noqa: ANN001
        _ = (check, text, capture_output)
        captured.append(list(cmd))
        if cmd[:2] == ["railway", "link"]:
            return _Result(0, stdout="{}")
        return _Result(0, stdout="logs")

    with (
        patch("app.remote.ops.shutil.which", return_value="/usr/local/bin/railway"),
        patch("app.remote.ops.subprocess.run", side_effect=_run),
    ):
        provider.fetch_logs(scope, lines=42)

    logs_cmd = next(c for c in captured if c[:2] == ["railway", "logs"])
    assert logs_cmd == ["railway", "logs", "--tail", "42"]
