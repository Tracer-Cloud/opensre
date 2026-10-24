"""REPL/gateway ``/remote-sync`` — same shared service as the CLI."""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

import pytest
from rich.console import Console

from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.engine import SyncProgress, SyncReport
from infrastructure.filestorage.enums import SyncDirection, SyncRootName
from infrastructure.filestorage.errors import RemoteSyncConfigError
from infrastructure.filestorage.operations import SyncRootStatus, SyncStatus
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.runtime.slash_adapter import headless_slash_ports
from tools.interactive_shell.shared.slash_catalog import MCP_BY_COMMAND


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def test_remote_sync_registered_in_slash_and_catalog() -> None:
    assert "/remote-sync" in SLASH_COMMANDS
    assert "/remote-sync" in MCP_BY_COMMAND
    assert "/sync" not in SLASH_COMMANDS


def test_gateway_headless_ports_expose_remote_sync() -> None:
    ports = headless_slash_ports()
    assert ports.command_exists("/remote-sync") is True
    assert ports.tty_interactive() is False


def test_status_off_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.get_sync_status",
        lambda: SyncStatus(config=None, roots=()),
    )
    console, buf = _capture()
    assert dispatch_slash("/remote-sync status", Session(), console) is True
    assert "Remote sync is off" in buf.getvalue()


def test_status_enabled_shows_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.get_sync_status",
        lambda: SyncStatus(
            config=RemoteSyncConfig(bucket="b", provider="aws", prefix="p"),
            roots=(SyncRootStatus(name=SyncRootName.SESSIONS, path=Path("/s"), exists=True),),
        ),
    )
    console, buf = _capture()
    assert dispatch_slash("/remote-sync", Session(), console) is True
    out = buf.getvalue()
    assert "Remote sync is on (aws)" in out
    assert "b/p" in out
    assert "sessions" in out


def test_sync_subcommand_calls_service(monkeypatch: pytest.MonkeyPatch) -> None:
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
        return SyncReport(uploaded=["memory/a.md"], skipped=0)

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.run_remote_sync",
        _run,
    )
    # console.status context manager — Rich Console.status works without a real TTY
    console, buf = _capture()
    assert dispatch_slash("/remote-sync sync --push-only", Session(), console) is True
    assert seen == {"pull_only": False, "push_only": True, "dry_run": False}
    assert "1 uploaded" in buf.getvalue()


def test_sync_dry_run_forwards_flag_and_labels_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, bool] = {}

    def _run(
        *,
        pull_only: bool = False,
        push_only: bool = False,
        dry_run: bool = False,
        on_progress: Callable[[SyncProgress], None] | None = None,
    ) -> SyncReport:
        del on_progress
        seen["dry_run"] = dry_run
        return SyncReport(uploaded=["sessions/a.jsonl"], skipped=0)

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.run_remote_sync",
        _run,
    )
    console, buf = _capture()
    assert dispatch_slash("/remote-sync sync --dry-run", Session(), console) is True
    assert seen == {"dry_run": True}
    out = buf.getvalue()
    assert "Dry run" in out
    assert "would be uploaded" in out


def test_sync_progress_callback_is_wired_through_a_direction_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service-supplied on_progress must work through a real Progress bar.

    Exercises both add_task (first PULL event) and reset (the PULL→PUSH
    direction change) without raising, against a non-terminal console —
    the same rendering path the gateway uses.
    """

    def _run(
        *,
        pull_only: bool = False,
        push_only: bool = False,
        dry_run: bool = False,
        on_progress: Callable[[SyncProgress], None] | None = None,
    ) -> SyncReport:
        assert on_progress is not None
        on_progress(SyncProgress(SyncDirection.PULL, "memory/a.md", 1, 1))
        on_progress(SyncProgress(SyncDirection.PUSH, "sessions/b.jsonl", 1, 1))
        return SyncReport(downloaded=["memory/a.md"], uploaded=["sessions/b.jsonl"])

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.run_remote_sync",
        _run,
    )
    console, buf = _capture()
    assert dispatch_slash("/remote-sync sync", Session(), console) is True
    # A non-terminal console (as used here and by the gateway) renders no live
    # progress output — only the final report reaches buf. Wiring correctness
    # is verified above by on_progress firing through both branches without
    # raising.
    assert "1 downloaded" in buf.getvalue()
    assert "1 uploaded" in buf.getvalue()


def test_sync_disabled_prints_help(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.run_remote_sync",
        lambda **_kwargs: None,
    )
    console, buf = _capture()
    assert dispatch_slash("/remote-sync sync", Session(), console) is True
    assert "Remote sync is off" in buf.getvalue()


def test_setup_requires_bucket_flag() -> None:
    console, buf = _capture()
    assert dispatch_slash("/remote-sync setup", Session(), console) is True
    assert "--bucket" in buf.getvalue()


def test_setup_writes_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from config.constants import paths as paths_mod

    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    console, buf = _capture()
    assert (
        dispatch_slash(
            "/remote-sync setup --provider vercel --bucket opensre-remote-sync",
            Session(),
            console,
        )
        is True
    )
    out = buf.getvalue()
    assert "settings saved" in out
    assert "vercel" in out
    assert "BLOB_READ_WRITE_TOKEN" in out


def test_setup_disabled_says_off_without_a_sync_suggestion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.constants import paths as paths_mod

    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    console, buf = _capture()
    assert (
        dispatch_slash(
            "/remote-sync setup --provider gcs --bucket b --disabled", Session(), console
        )
        is True
    )
    out = buf.getvalue()
    assert "Remote sync is off" in out
    assert "remote-sync sync" not in out


def test_sync_error_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kwargs: object) -> SyncReport:
        raise RemoteSyncConfigError("bad flags")

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.run_remote_sync",
        _boom,
    )
    console, buf = _capture()
    assert dispatch_slash("/remote-sync sync --pull-only --push-only", Session(), console) is True
    out = buf.getvalue()
    assert "failed" in out.lower()
    # This handler also serves gateway chat, so provider detail must not appear.
    assert "bad flags" not in out, "error detail reached the chat reply"


def test_unknown_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    console, buf = _capture()
    assert dispatch_slash("/remote-sync nope", Session(), console) is True
    assert "unknown subcommand" in buf.getvalue()


def test_gateway_dispatch_uses_same_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.get_sync_status",
        lambda: SyncStatus(config=None, roots=()),
    )
    ports = headless_slash_ports()
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


def test_slash_command_metadata_for_planner() -> None:
    cmd = SLASH_COMMANDS["/remote-sync"]
    assert cmd.first_arg_completions is not None
    labels = {label for label, _hint in cmd.first_arg_completions}
    assert labels == {"status", "sync", "setup"}
    assert any("setup" in note.lower() for note in (cmd.notes or ()))
    catalog = MCP_BY_COMMAND["/remote-sync"]
    assert "status" in catalog.llm_description
    assert "setup" in catalog.llm_description


def test_sync_shows_kept_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.run_remote_sync",
        lambda **_kwargs: SyncReport(kept_remote=["sessions/newer.jsonl"]),
    )
    console, buf = _capture()
    assert dispatch_slash("/remote-sync sync --push-only", Session(), console) is True
    out = buf.getvalue()
    assert "sessions/newer.jsonl" in out
    assert "newer copy" in out.lower() or "push-only" in out.lower()


def test_help_section_includes_remote_sync() -> None:
    from surfaces.interactive_shell.command_registry.help import _help_sections

    sections = dict(_help_sections())
    assert "/remote-sync" in {c.name for c in sections["Remote sync"]}


def test_repl_and_headless_agent_same_command_object() -> None:
    """Gateway headless ports must not register a fork of /remote-sync."""
    from surfaces.interactive_shell.runtime.slash_adapter import repl_slash_ports

    repl = repl_slash_ports()
    headless = headless_slash_ports()
    assert repl.command_exists("/remote-sync")
    assert headless.command_exists("/remote-sync")
    assert SLASH_COMMANDS["/remote-sync"].handler is not None


# ---------------------------------------------------------------------------
# Regression: flag with missing value must not be saved as "--next-flag"
# ---------------------------------------------------------------------------


def test_setup_rejects_bucket_flag_followed_by_another_flag(tmp_path: object) -> None:
    """--bucket --prefix should error, not store '--prefix' as the bucket name."""
    console, buf = _capture()
    assert dispatch_slash("/remote-sync setup --bucket --prefix team/", Session(), console) is True
    out = buf.getvalue()
    assert "requires a value" in out.lower() or "--bucket" in out


def test_setup_rejects_region_flag_followed_by_another_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--region --profile must error and not write anything."""
    from config.constants import paths as paths_mod

    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    console, buf = _capture()
    assert (
        dispatch_slash(
            "/remote-sync setup --bucket real-bucket --region --profile prod",
            Session(),
            console,
        )
        is True
    )
    out = buf.getvalue()
    assert "requires a value" in out.lower()
    # Nothing written to config
    config_file = tmp_path / "config.yml"
    assert not config_file.exists()


def test_setup_flag_equals_value_still_works(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--bucket=name form must keep working after the validation change."""
    from config.constants import paths as paths_mod

    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    console, buf = _capture()
    assert (
        dispatch_slash(
            "/remote-sync setup --bucket=my-bucket --provider=aws",
            Session(),
            console,
        )
        is True
    )
    assert "settings saved" in buf.getvalue()
