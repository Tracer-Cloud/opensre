"""Gateway surface: ``/remote-sync`` via headless slash ports from gateway_entry."""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.engine import SyncProgress, SyncReport
from infrastructure.filestorage.enums import SyncRootName
from infrastructure.filestorage.errors import RemoteSyncConfigError
from infrastructure.filestorage.operations import SyncRootStatus, SyncStatus
from surfaces.gateway_entry import gateway_slash_ports_factory, start_gateway
from surfaces.interactive_shell.runtime import Session


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def test_gateway_slash_ports_factory_is_headless_and_exposes_remote_sync() -> None:
    ports = gateway_slash_ports_factory()
    assert ports.command_exists("/remote-sync") is True
    assert ports.tty_interactive() is False
    # Headless outcomes must stay plain text for Slack/Telegram sinks.
    outcome = ports.format_turn_outcome("/remote-sync", ok=True)
    assert "[" not in outcome and "]" not in outcome
    assert "succeeded" in outcome


def test_start_gateway_injects_remote_sync_capable_factory() -> None:
    """Changing gateway_entry must keep slash ports wired for chat turns."""
    import gateway.core.lifecycle.controller as manager_module

    mock_manager = MagicMock()
    mock_manager.start_gateway.return_value = mock_manager
    with patch.object(manager_module, "GatewayController", return_value=mock_manager) as ctor:
        start_gateway(wait=False)

    factory = ctor.call_args.kwargs["slash_ports_factory"]
    assert factory is gateway_slash_ports_factory
    ports = factory()
    assert ports.command_exists("/remote-sync") is True


def test_gateway_dispatch_status_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.get_sync_status",
        lambda: SyncStatus(config=None, roots=()),
    )
    ports = gateway_slash_ports_factory()
    console, buf = _capture()
    ok = ports.dispatch(
        "/remote-sync status",
        session=Session(),
        console=console,
        confirm_fn=None,
        is_tty=False,
    )
    assert ok is True
    assert "Remote sync is off" in buf.getvalue()


def test_gateway_dispatch_status_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.get_sync_status",
        lambda: SyncStatus(
            config=RemoteSyncConfig(bucket="gw-bucket", provider="aws", prefix="chat"),
            roots=(
                SyncRootStatus(name=SyncRootName.SESSIONS, path=Path("/u/s"), exists=True),
                SyncRootStatus(name=SyncRootName.MEMORY, path=Path("/u/m"), exists=True),
            ),
        ),
    )
    ports = gateway_slash_ports_factory()
    console, buf = _capture()
    assert (
        ports.dispatch(
            "/remote-sync",
            session=Session(),
            console=console,
            confirm_fn=None,
            is_tty=False,
        )
        is True
    )
    out = buf.getvalue()
    assert "Remote sync is on (aws)" in out
    assert "gw-bucket/chat" in out


def test_gateway_dispatch_sync_with_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, bool] = {}

    def _run(
        *,
        pull_only: bool = False,
        push_only: bool = False,
        dry_run: bool = False,
        on_progress: Callable[[SyncProgress], None] | None = None,
    ) -> SyncReport:
        del on_progress
        seen["pull_only"] = pull_only
        seen["push_only"] = push_only
        seen["dry_run"] = dry_run
        return SyncReport(
            uploaded=["sessions/a.jsonl"],
            downloaded=["memory/b.md"],
            kept_remote=["sessions/newer.jsonl"],
            skipped=1,
        )

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.run_remote_sync",
        _run,
    )
    ports = gateway_slash_ports_factory()
    console, buf = _capture()
    assert (
        ports.dispatch(
            "/remote-sync sync --pull-only",
            session=Session(),
            console=console,
            confirm_fn=None,
            is_tty=False,
        )
        is True
    )
    assert seen == {"pull_only": True, "push_only": False, "dry_run": False}
    out = buf.getvalue()
    assert "1 downloaded" in out
    assert "1 uploaded" in out
    assert "sessions/newer.jsonl" in out


def test_gateway_dispatch_sync_error_stays_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handled, and the provider's own words never reach the chat reply.

    Gateway replies are an external surface (CWE-209). A bucket name, region,
    or credential hint inside a provider error must not be echoed to a chat
    member; the detail belongs in the server-side log.
    """
    leaked = "s3://private-bucket-name-and-region-eu-west-2"

    def _boom(**_kwargs: object) -> SyncReport:
        raise RemoteSyncConfigError(leaked)

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.run_remote_sync",
        _boom,
    )
    ports = gateway_slash_ports_factory()
    console, buf = _capture()
    # Must not raise into the gateway turn loop.
    assert (
        ports.dispatch(
            "/remote-sync sync",
            session=Session(),
            console=console,
            confirm_fn=None,
            is_tty=False,
        )
        is True
    )
    out = buf.getvalue()
    assert "Remote-sync command failed" in out
    assert leaked not in out, "provider detail reached the gateway reply"
