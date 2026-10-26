"""Record list adapters share search without changing non-interactive output."""

from __future__ import annotations

import io
import threading
from pathlib import Path

import pytest
from prompt_toolkit.application import Application, create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from config.constants import FORCE_COLOR_ENV, OPENSRE_MEMORY_DIR_ENV
from core.domain.memory import save_memory
from infrastructure.scheduling.scheduler.loops import summarize_loop
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from infrastructure.scheduling.task_types import TaskKind as RuntimeTaskKind
from surfaces.interactive_shell.command_registry import cron_cmds, dispatch_slash, input_collection
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.streaming.console import StreamingConsole
from surfaces.shared.terminal.components import record_browser
from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow, RecordTable


class _Spinner:
    bytes_in = 0
    streaming = False

    def stop(self) -> None:
        """Satisfy the streaming-console progress contract."""


def _wide_record_table() -> RecordTable:
    return RecordTable(
        "Scheduled tasks",
        tuple(
            RecordColumn(label)
            for label in ("Task", "Channel", "State", "Schedule", "TZ", "Next run")
        ),
        (
            RecordRow(
                tuple(
                    Text(value)
                    for value in (
                        "Long daily audit task",
                        "telegram, local, slack",
                        "Active",
                        "0 8 * * MON-FRI",
                        "Asia/Kolkata",
                        "2026-10-15 08:00:00",
                    )
                ),
                metadata=(Text("ID: schedule-123"),),
            ),
        ),
    )


@pytest.mark.parametrize("command", ["/memory list", "/loops list", "/cron list", "/tasks"])
@pytest.mark.parametrize("interactive", [True, False])
def test_record_lists_use_browser_without_reprinting_on_close(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str, interactive: bool
) -> None:
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    save_memory(
        slug="prod-cluster",
        memory_type="infrastructure",
        description="Unique detail",
        body="Full body",
    )
    loop = summarize_loop(
        ScheduledTask(
            id="full-loop-id",
            name="Daily audit",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 8 * * *",
            provider=Provider.SLACK,
        )
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.loops.list_loop_summaries", lambda **_kw: [loop]
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.loop_results.latest_loop_runs", lambda _loops: {}
    )
    monkeypatch.setattr(cron_cmds, "list_loop_summaries", lambda **_kw: [loop])
    monkeypatch.setattr(input_collection, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(
        "surfaces.shared.terminal.tables.schedule_listing.latest_loop_runs", lambda _loops: {}
    )
    session = Session()
    session.terminal.exclusive_stdin_active = True
    task = session.task_registry.create(RuntimeTaskKind.CLI_COMMAND, command="Check rollout")
    task.mark_running()
    task.mark_failed("[literal] unavailable")
    browsed: list[bool] = []

    def run_browser(app: Application[None]) -> None:
        browsed.append(True)
        with create_pipe_input() as pipe:
            app.input = pipe
            app.output = DummyOutput()
            app.run(pre_run=lambda: pipe.send_text("\x1b"))

    monkeypatch.setattr(record_browser, "run_command_input", run_browser)
    output = io.StringIO()
    with create_app_session(output=DummyOutput()):
        assert dispatch_slash(
            command,
            session,
            Console(file=output, force_terminal=True, width=90),
            is_tty=interactive,
        )
    assert bool(browsed) is interactive
    expected = (
        "prod-cluster" if "memory" in command else task.task_id if command == "/tasks" else loop.id
    )
    if interactive:
        assert expected not in output.getvalue()
    else:
        assert expected in output.getvalue()
        if command == "/tasks":
            assert "[literal] unavailable" in output.getvalue()
        if "memory" in command:
            assert "unencrypted" in output.getvalue()


def test_forced_color_redirected_output_never_opens_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FORCE_COLOR_ENV, "0")
    monkeypatch.setattr(input_collection, "repl_tty_interactive", lambda: True)
    session = Session()
    session.terminal.exclusive_stdin_active = True
    task = session.task_registry.create(RuntimeTaskKind.CLI_COMMAND, command="Check rollout")

    def unexpected_browser(_app: Application[None]) -> None:
        pytest.fail("Redirected output must not read from the terminal")

    monkeypatch.setattr(record_browser, "run_command_input", unexpected_browser)
    output = io.StringIO()
    destination = Console(file=output, width=90)
    assert destination.is_terminal
    console = StreamingConsole(
        _Spinner(),
        threading.Event(),
        output=destination,
        force_terminal=destination.is_terminal,
    )
    assert dispatch_slash("/tasks", session, console, is_tty=True)
    assert task.task_id in output.getvalue()
    assert "┌" not in output.getvalue()


def test_browser_searches_details_without_inventing_ids() -> None:
    table = RecordTable(
        "Memories",
        (RecordColumn("Name"),),
        (
            RecordRow(
                (Text("prod-cluster"),),
                details=(Text("First detail"), Text("x" * 130 + " Unique second detail")),
            ),
        ),
        subtitle="List-level context belongs with the columns, not the selected record.",
    )
    frames: list[str] = []
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        app = record_browser.build_record_browser(table, title="/memory list")

        def capture(_app: Application[None]) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None or len(frames) == 3:
                return
            content = "\n".join(
                "".join(screen.data_buffer[y][x].char for x in range(80))
                for y in range(screen.height)
            )
            assert "ID:" not in content
            assert content.count("List-level context") == 1
            if len(frames) < 2:
                assert "First detail" in content
            if not frames:
                assert "Unique second detail" not in content
            elif len(frames) == 1:
                assert "Unique second detail" in content
                assert content.index("Unique second detail") < content.index("First detail")
            if not frames:
                pipe.send_text("UNIQUE")
            elif len(frames) == 1:
                assert "1 of 1 items" in content
                pipe.send_text("\x150")
            else:
                assert "0 of 1 items" in content
                pipe.send_text("\x1b")
            frames.append(content)

        app.after_render += capture
        assert app.run() is None
    assert len(frames) == 3


def test_compact_browser_shows_the_detail_that_matched_search() -> None:
    class CompactOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=15, columns=40)

    table = RecordTable(
        "Memories",
        (RecordColumn("Name"),),
        (
            RecordRow(
                (Text("prod-cluster"),),
                details=(Text("First detail"), Text("Unique second detail")),
            ),
        ),
    )
    frames: list[str] = []
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=CompactOutput()):
        app = record_browser.build_record_browser(table, title="/memory list")

        def capture(_app: Application[None]) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None or len(frames) == 2:
                return
            content = "\n".join(
                "".join(screen.data_buffer[y][x].char for x in range(40))
                for y in range(screen.height)
            )
            if not frames:
                assert "Unique second detail" not in content
                pipe.send_text("UNIQUE")
            else:
                assert "Unique second detail" in content
                pipe.send_text("\x1b")
            frames.append(content)

        app.after_render += capture
        assert app.run() is None
    assert len(frames) == 2


def _render_browser(table: RecordTable, *, width: int, title: str) -> str:
    class SizedOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=30, columns=width)

    frames: list[str] = []
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=SizedOutput()):
        app = record_browser.build_record_browser(table, title=title)

        def capture(_app: Application[None]) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None or frames:
                return
            frames.append(
                "\n".join(
                    "".join(screen.data_buffer[y][x].char for x in range(width))
                    for y in range(screen.height)
                )
            )
            pipe.send_text("\x03")

        app.after_render += capture
        assert app.run() is None
    assert len(frames) == 1
    return frames[0]


@pytest.mark.parametrize(("width", "expect_columns"), [(40, False), (90, False), (160, True)])
def test_record_browser_uses_available_space_before_stacking(
    width: int, expect_columns: bool
) -> None:
    text = _render_browser(_wide_record_table(), width=width, title="/cron list")
    lines = text.splitlines()
    border = next(line for line in lines if line.startswith("┌"))
    frame_width = cell_len(border.rstrip())
    assert frame_width <= width
    if expect_columns:
        assert frame_width > 96
        header = next(line for line in lines if "Channel" in line)
        assert all(label in header for label in ("Task", "State", "Schedule", "TZ", "Next run"))
        assert "Channel:" not in text
    else:
        assert "Channel:" in text and "Next run:" in text


def test_compact_record_browser_stays_bounded_on_wide_terminal() -> None:
    table = RecordTable(
        "Memories",
        (RecordColumn("Name"), RecordColumn("Type")),
        (RecordRow((Text("prod-cluster"), Text("infrastructure"))),),
    )
    text = _render_browser(table, width=160, title="/memory list")
    border = next(line for line in text.splitlines() if line.startswith("┌"))
    assert cell_len(border.rstrip()) == 96


def test_oversized_record_browser_caps_at_terminal_and_reflows() -> None:
    table = RecordTable(
        "Records",
        (RecordColumn("Name"), RecordColumn("Detail")),
        (RecordRow((Text("audit"), Text("long detail " * 30))),),
    )
    text = _render_browser(table, width=160, title="/records")
    border = next(line for line in text.splitlines() if line.startswith("┌"))
    assert cell_len(border.rstrip()) == 160
    assert "Detail:" in text
