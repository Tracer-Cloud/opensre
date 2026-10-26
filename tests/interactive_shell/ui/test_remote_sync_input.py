"""Remote-sync forms edit drafts without touching settings or cloud storage."""

from pathlib import Path

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from config.constants import paths
from config.local_settings import read_section, update_section
from infrastructure.filestorage.setup import RemoteSyncSetupRequest
from surfaces.interactive_shell.ui.remote_sync_input import build_remote_sync_form


def test_form_prefills_stored_settings_and_cancel_never_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    update_section("remote_sync", {"provider": "gcs", "bucket": "stored", "enabled": False})
    original = (tmp_path / "config.yml").read_bytes()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        app = build_remote_sync_form({"prefix": "team/"})
        result = app.run(pre_run=lambda: pipe.send_text("\x13"))
        assert result == RemoteSyncSetupRequest(
            provider="gcs", bucket="stored", prefix="team/", enabled=False
        )
        app = build_remote_sync_form({})
        assert app.run(pre_run=lambda: pipe.send_text("\x03")) is None
    assert (tmp_path / "config.yml").read_bytes() == original


def test_provider_change_drops_hidden_unsupported_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    values = {"provider": "aws", "bucket": "demo", "profile": "prod", "region": "us-east-1"}
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        app = build_remote_sync_form(values)
        result = app.run(pre_run=lambda: pipe.send_text("\rvercel\r\x13"))
    assert result == RemoteSyncSetupRequest(provider="vercel", bucket="demo")
    assert values["profile"] == "prod"
    assert not read_section("remote_sync")


def test_validation_error_retains_draft_until_corrected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        app = build_remote_sync_form({"provider": "vercel", "bucket": "demo", "region": "wrong"})
        # Submit reopens the unsupported field; clearing it retains the destination.
        result = app.run(pre_run=lambda: pipe.send_text("\x13\x15\r\x1b\x13"))
    assert result == RemoteSyncSetupRequest(provider="vercel", bucket="demo")
    assert not (tmp_path / "config.yml").exists()


@pytest.mark.parametrize("width", [40, 90, 160])
def test_form_rendering_keeps_fields_and_actions_readable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, width: int
) -> None:
    from prompt_toolkit.application import Application
    from prompt_toolkit.data_structures import Size

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)

    class SizedOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=32, columns=width)

    frames: list[str] = []
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=SizedOutput()):
        app = build_remote_sync_form(
            {"provider": "aws", "bucket": "[literal] 日本 " + "long-" * 20}
        )

        def capture(current: Application[RemoteSyncSetupRequest | None]) -> None:
            screen = current.renderer.last_rendered_screen
            if screen is None or len(frames) >= 2:
                return
            content = "\n".join(
                "".join(screen.data_buffer[y][x].char for x in range(width))
                for y in range(screen.height)
            )
            frames.append(content)
            pipe.send_text("\x1b[B\r" if len(frames) == 1 else "\x03")

        app.after_render += capture
        assert app.run() is None
    assert len(frames) == 2
    content = "\n".join(frames)
    assert "Provider" in content and "Bucket/store" in content
    assert "required" in content
    assert "Optional settings" in content and "Save settings" in content and "Cancel" in content
    assert "[literal]" in content
    assert "prod" not in content


@pytest.mark.parametrize("surface", ["cli", "cli-incomplete", "slash"])
def test_real_pty_setup_returns_cleanly_to_next_editable_prompt(
    tmp_path: Path, surface: str
) -> None:
    import os
    import select
    import struct
    import subprocess
    import sys
    import time

    pty = pytest.importorskip("pty")
    termios = pytest.importorskip("termios")
    fcntl = pytest.importorskip("fcntl")
    child = """
import sys
from pathlib import Path
from config.constants import paths
paths.OPENSRE_HOME_DIR = Path(sys.argv[1])
from config.local_settings import read_section
if sys.argv[2].startswith("cli"):
    incomplete = sys.argv[2] == "cli-incomplete"
    from surfaces.cli.commands.remote_sync import remote_sync_command
    try:
        args = ["setup"] if incomplete else ["setup", "--provider", "vercel", "--bucket", "pty-store"]
        remote_sync_command.main(args)
    except SystemExit as exc:
        assert (exc.code != 0) if incomplete else (exc.code == 0)
else:
    from rich.console import Console
    from surfaces.interactive_shell.command_registry import dispatch_slash
    from surfaces.interactive_shell.runtime import Session, input_policy
    session = Session()
    command = "/remote-sync setup"
    session.terminal.exclusive_stdin_active = input_policy.turn_needs_exclusive_stdin(command, session)
    dispatch_slash(command, session, Console(), is_tty=True)
from prompt_toolkit import PromptSession
print("FOLLOWUP:" + PromptSession().prompt("Next input: "))
if sys.argv[2] == "cli-incomplete":
    assert not read_section("remote_sync")
else:
    assert read_section("remote_sync")["bucket"] == "pty-store"
"""
    master, slave = pty.openpty()
    process: subprocess.Popen[bytes] | None = None
    captured = bytearray()
    pending = bytearray()
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 90, 0, 0))
        process = subprocess.Popen(
            [sys.executable, "-c", child, str(tmp_path), surface],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env={**os.environ, "TERM": "xterm-256color"},
        )

        def expect(marker: bytes) -> None:
            deadline = time.monotonic() + 20
            while marker not in pending:
                assert time.monotonic() < deadline, captured.decode(errors="replace")
                if select.select([master], [], [], 0.1)[0]:
                    chunk = os.read(master, 65536)
                    captured.extend(chunk)
                    pending.extend(chunk)
                    # Emulate the terminal's cursor-position reply, not keystrokes.
                    if b"\x1b[6n" in chunk:
                        os.write(master, b"\x1b[1;1R")
            del pending[: pending.index(marker) + len(marker)]

        if surface == "slash":
            expect(b"Type to filter")
            os.write(master, b"vercel\r")
            expect(b"Bucket/store")
            os.write(master, b"\x1b[B\r")
            expect(b"Name of an existing")
            os.write(master, b"pty-store\r\x13")
        expect(b"setup needs --bucket" if surface == "cli-incomplete" else b"settings saved")
        expect(b"Next input:")
        os.write(master, b"next-prompt-works\r")
        expect(b"FOLLOWUP:next-prompt-works")
        assert process.wait(timeout=10) == 0, captured.decode(errors="replace")
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize("surface", ["cli", "slash"])
def test_complete_destination_uses_same_default_provider_without_form(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, surface: str
) -> None:
    import io

    from click.testing import CliRunner
    from rich.console import Console

    from surfaces.cli.app import cli
    from surfaces.interactive_shell.command_registry import dispatch_slash
    from surfaces.interactive_shell.runtime import Session

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)

    def unexpected(_app: object) -> None:
        raise AssertionError("Complete settings must not open a form on either surface")

    monkeypatch.setattr("click.prompt", unexpected)
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.run_command_input", unexpected
    )
    if surface == "cli":
        result = CliRunner().invoke(cli, ["remote-sync", "setup", "--bucket", "demo"])
        assert result.exit_code == 0, result.output
    else:
        dispatch_slash(
            "/remote-sync setup --bucket demo", Session(), Console(file=io.StringIO()), is_tty=False
        )
    assert read_section("remote_sync") == {
        "provider": "aws",
        "bucket": "demo",
        "prefix": "opensre",
        "region": "",
        "profile": "",
        "enabled": True,
    }
