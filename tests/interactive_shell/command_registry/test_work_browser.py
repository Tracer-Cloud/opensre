"""Read-only work browsing shares picker search without accepting an action."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from prompt_toolkit.application import Application, create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from config.constants import OPENSRE_WORK_ITEMS_DIR_ENV
from core.domain.work_items import add_work_item, work_items_path
from surfaces.interactive_shell.command_registry import dispatch_slash, input_collection
from surfaces.interactive_shell.session import Session
from surfaces.shared.terminal.components import record_browser
from surfaces.shared.terminal.tables.work_items import work_items_table


@pytest.mark.parametrize("width", [40, 90, 160])
def test_work_browser_filters_records_and_enter_never_submits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, width: int
) -> None:
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path))
    first = add_work_item(title="[literal] 日本 checkout", project="Payments")
    second = add_work_item(title="Verify rollout", project="Platform")
    before = work_items_path().read_bytes()

    class SizedOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=30, columns=width)

    stages = [
        ("", "2 of 2 items", "PAYMENTS"),
        ("PAYMENTS", "1 of 2 items", "\r\x13\x15absent"),
        ("absent", "0 of 2 items", "\r\x15" + second.id),
        (second.id, "1 of 2 items", "\x15[literal]"),
        ("[literal]", "1 of 2 items", "\x1b"),
    ]
    frames: list[str] = []
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=SizedOutput()):
        app = record_browser.build_record_browser(
            work_items_table([first, second]), title="/work list"
        )

        def capture(_app: Application[None]) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None or len(frames) >= len(stages):
                return
            query, count, keys = stages[len(frames)]
            if app.current_buffer.text != query:
                return
            lines = [
                "".join(screen.data_buffer[y][x].char for x in range(width))
                for y in range(screen.height)
            ]
            content = "\n".join(lines)
            assert count in content
            assert "Esc close" in content and "Enter" not in content
            assert "Esc closes to the full list" not in content
            assert content.count("Due times: UTC") == 1
            assert content.index("Due times: UTC") < content.index("Search ›")
            assert "Search ›" in content
            assert max(len(line.rstrip()) for line in lines) <= min(width, 96)
            if query == "absent":
                assert "No matches" in content
            elif query == second.id:
                assert "Verify rollout" in content and second.id in content
            frames.append(content)
            pipe.send_text(keys)

        app.after_render += capture
        assert app.run() is None
    assert len(frames) == len(stages)
    assert work_items_path().read_bytes() == before


@pytest.mark.parametrize(
    "command,tty,owns_stdin,expected_browser",
    [
        ("/work", True, True, True),
        ("/work list all Payments", True, True, True),
        ("/work ls", True, True, True),
        ("/work list", False, True, False),
        ("/work list", True, False, False),
    ],
)
def test_work_browser_dispatch_preserves_filters_and_noninteractive_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    command: str,
    tty: bool,
    owns_stdin: bool,
    expected_browser: bool,
) -> None:
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path))
    first = add_work_item(title="Check payments", project="Payments")
    second = add_work_item(title="Check platform", project="Platform")
    before = work_items_path().read_bytes()
    monkeypatch.setattr(input_collection, "repl_tty_interactive", lambda: True)
    browsed: list[bool] = []

    def run_browser(app: Application[None]) -> None:
        browsed.append(True)
        with create_pipe_input() as pipe:
            app.input = pipe
            app.output = DummyOutput()
            app.run(pre_run=lambda: pipe.send_text("\r\x13\x1b"))

    monkeypatch.setattr(record_browser, "run_command_input", run_browser)
    session = Session()
    session.terminal.exclusive_stdin_active = owns_stdin
    output = io.StringIO()
    console = Console(file=output, width=90, force_terminal=True, color_system=None)
    assert dispatch_slash(command, session, console, is_tty=tty)
    assert bool(browsed) is expected_browser
    assert ("┌" in output.getvalue()) is expected_browser
    assert first.id in output.getvalue()
    assert (second.id in output.getvalue()) is ("Payments" not in command)
    assert work_items_path().read_bytes() == before
