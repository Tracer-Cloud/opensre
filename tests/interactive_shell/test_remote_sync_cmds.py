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


@pytest.mark.parametrize(
    ("flags", "missing"),
    [
        ("--provider vercel --bucket --prefix team/", "bucket"),
        ("--provider aws --bucket real-bucket --region --profile prod", "region"),
        ("--bucket --disabled", "bucket"),
        ("--bucket real-bucket --profile", "profile"),
    ],
)
def test_setup_missing_flag_value_never_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flags: str, missing: str
) -> None:
    from config.constants import paths

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    console, output = _capture()
    dispatch_slash(f"/remote-sync setup {flags}", Session(), console)
    assert f"--{missing} requires a value" in output.getvalue()
    assert not (tmp_path / "config.yml").exists()


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("flags", ["--prefix=team/", "--disabled --enabled --prefix=team/"])
def test_terminal_setup_uses_exclusive_form_and_saves_only_after_submit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cancel: bool, flags: str
) -> None:
    from prompt_toolkit.application import Application, create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from config.constants import paths
    from config.local_settings import read_section
    from infrastructure.filestorage.setup import RemoteSyncSetupRequest
    from surfaces.interactive_shell.command_registry import input_collection, remote_sync_cmds
    from surfaces.interactive_shell.runtime import input_policy

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.setattr(input_policy, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(input_collection, "repl_tty_interactive", lambda: True)

    def collect(app: Application[RemoteSyncSetupRequest | None]) -> RemoteSyncSetupRequest | None:
        assert not (tmp_path / "config.yml").exists()
        with create_pipe_input() as pipe:
            app.input, app.output = pipe, DummyOutput()
            keys = "vercel\r\x1b[B\rteam-store\r" + ("\x03" if cancel else "\x13")
            return app.run(pre_run=lambda: pipe.send_text(keys))

    monkeypatch.setattr(remote_sync_cmds, "run_command_input", collect)
    command = f"/remote-sync setup {flags}"
    session = Session()
    session.terminal.exclusive_stdin_active = input_policy.turn_needs_exclusive_stdin(
        command, session
    )
    assert session.terminal.exclusive_stdin_active
    console, output = _capture()
    with create_app_session(output=DummyOutput()):
        dispatch_slash(command, session, console, is_tty=True)
    if cancel:
        assert not (tmp_path / "config.yml").exists()
        assert "saved" not in output.getvalue()
    else:
        assert read_section("remote_sync") == {
            "provider": "vercel",
            "bucket": "team-store",
            "prefix": "team/",
            "region": "",
            "profile": "",
            "enabled": True,
        }


def test_gateway_complete_flags_bypass_form_and_incomplete_flags_never_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.constants import paths
    from config.local_settings import read_section
    from surfaces.interactive_shell.command_registry import remote_sync_cmds

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)

    def unexpected(_app: object) -> None:
        raise AssertionError("Gateway must not open a terminal form")

    monkeypatch.setattr(remote_sync_cmds, "run_command_input", unexpected)
    ports = headless_slash_ports()
    console, output = _capture()
    ports.dispatch(
        "/remote-sync setup", session=Session(), console=console, confirm_fn=None, is_tty=False
    )
    assert "setup needs --bucket" in output.getvalue()
    assert not (tmp_path / "config.yml").exists()
    ports.dispatch(
        "/remote-sync setup --provider=vercel --bucket=team-store --prefix=",
        session=Session(),
        console=console,
        confirm_fn=None,
        is_tty=False,
    )
    assert read_section("remote_sync")["prefix"] == "opensre"
    ports.dispatch(
        "/remote-sync setup --disabled",
        session=Session(),
        console=console,
        confirm_fn=None,
        is_tty=False,
    )
    assert read_section("remote_sync")["enabled"] is False
    assert read_section("remote_sync")["bucket"] == "team-store"


@pytest.mark.parametrize(
    ("args", "needs_input"),
    [
        (["setup"], True),
        (["setup", "--provider=vercel", "--prefix=team/"], True),
        (["setup", "--bucket=store"], False),
        (["setup", "--disabled"], False),
        (["setup", "--bucket", "--disabled"], False),
        (["setup", "--disabled", "--enabled"], True),
        (["setup", "--enabled", "--disabled"], False),
        (["setup", "--bucket=store", "--disabled", "--enabled"], False),
        (["setup", "--disabled", "--enabled", "--bucket=store"], False),
    ],
)
def test_remote_sync_input_metadata_preserves_complete_command_bypass(
    args: list[str], needs_input: bool
) -> None:
    from config.command_inputs import needs_command_input

    assert needs_command_input("/remote-sync", args) is needs_input
