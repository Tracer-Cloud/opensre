"""Behavioral coverage for the purpose-first terminal tool browser."""

from __future__ import annotations

import io
from os import terminal_size
from types import SimpleNamespace

import pytest
from prompt_toolkit.output.base import DummyOutput, Size
from prompt_toolkit.output.vt100 import Vt100_Output
from rich.console import Console
from rich.text import Text

from config.constants.product import OPENSRE_INTERACTIVE_ENV
from surfaces.interactive_shell.command_registry import dispatch_slash, tools_cmds
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import tool_browser
from surfaces.shared.terminal.prompt_layout import prompt_text_width
from surfaces.shared.terminal.tables.tool_catalog import ToolCatalogEntry, ToolParameter


def _entry(name: str, description: str = "Find matching code.") -> ToolCatalogEntry:
    return ToolCatalogEntry(
        name,
        ("chat", "action"),
        description,
        "",
        "query: string",
        (ToolParameter("query", "string", True),),
    )


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    rows: list[str] = []
    frames: list[list[str]] = []
    events: list[str] = []
    state = SimpleNamespace(columns=80, lines=24, actions=iter(["cancel"]))

    def _read_key() -> str:
        frames.append(rows.copy())
        action = next(state.actions)
        key = action() if callable(action) else action
        assert isinstance(key, str)
        return key

    class _Output(DummyOutput):
        def enter_alternate_screen(self) -> None:
            events.append("screen")

        def cursor_goto(self, row: int = 0, column: int = 0) -> None:
            assert row == column == 0
            rows.clear()

        def write_raw(self, data: str) -> None:
            rows.append(data.rstrip("\r\n"))

        def quit_alternate_screen(self) -> None:
            events.append("restore")

    def _size(**_kwargs: object) -> terminal_size:
        return terminal_size((state.columns, state.lines))

    monkeypatch.setattr(tool_browser, "get_terminal_size", _size)
    monkeypatch.setattr(tool_browser, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(tool_browser, "enter_inline_menu", lambda: events.append("enter"))
    monkeypatch.setattr(tool_browser, "leave_inline_menu", lambda: events.append("leave"))
    monkeypatch.setattr(tool_browser, "create_output", _Output)
    monkeypatch.setattr(tool_browser, "read_menu_or_char", _read_key)
    state.frames, state.events = frames, events
    return state


def _plain(frame: list[str]) -> str:
    return "\n".join(Text.from_ansi(row).plain for row in frame)


def test_browse_has_compact_rows_and_a_fixed_selected_tool_preview(
    terminal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = [_entry("search_github"), _entry("fleet_scan", "Find unhealthy services.")]
    terminal.actions = iter(["enter", "enter", "down", "enter", "cancel"])
    monkeypatch.setattr(tools_cmds, "build_tool_catalog", lambda: entries)
    monkeypatch.setattr(tools_cmds, "repl_tty_interactive", lambda: True)
    output = io.StringIO()

    assert tools_cmds._cmd_tools(Session(), Console(file=output, force_terminal=True), []) is True

    initial, expanded, collapsed, moved, second = map(_plain, terminal.frames)
    assert "Find matching code." in initial and "Find unhealthy services." in initial
    assert "chat · action" not in initial
    initial_rows = initial.splitlines()
    assert any("search_github" in row and "Find matching code." in row for row in initial_rows)
    assert any("fleet_scan" in row and "Find unhealthy services." in row for row in initial_rows)
    assert "TOOL" in initial and "PURPOSE" in initial
    assert "parameter" not in initial and "query: string" not in initial
    assert "▾ search_github" in expanded and "parameters" in expanded
    assert "query: string" in expanded and "description" in expanded
    assert "query: string" not in collapsed
    assert "› fleet_scan" in moved and "▾ fleet_scan" in second
    for frame in (initial, expanded, collapsed, moved, second):
        # The preview is below the list, not inserted into the selected row.
        assert (
            frame.splitlines()[4]
            .strip()
            .startswith(("› search_github", "▾ search_github", "search_github"))
        )
        assert frame.splitlines()[8].strip() in ("search_github", "fleet_scan")
    assert len({len(frame) for frame in terminal.frames}) == 1
    assert output.getvalue() == ""
    assert terminal.events[0] == "enter"
    assert terminal.events[-2:] == ["restore", "leave"]


@pytest.mark.parametrize(("height", "visible"), [(24, 9), (30, 15), (60, 20)])
def test_list_grows_with_terminal_height_without_expanding_the_preview(
    terminal: SimpleNamespace, height: int, visible: int
) -> None:
    terminal.lines = height
    terminal.actions = iter(["up", "enter", "cancel"])
    tool_browser.browse_tools([_entry(f"tool_{index}") for index in range(30)])

    for frame in terminal.frames:
        rows = [Text.from_ansi(row).plain for row in frame]
        tool_rows = [row for row in rows if "Find matching code." in row and "tool_" in row]
        assert len(tool_rows) == visible
        assert len(rows) < height
        assert rows[-3] == ""
    assert "› tool_29" in _plain(terminal.frames[1])
    assert "▾ tool_29" in _plain(terminal.frames[2])
    assert len({len(frame) for frame in terminal.frames}) == 1


def test_long_details_can_be_read_forward_and_backward_without_changing_tool(
    terminal: SimpleNamespace,
) -> None:
    description = "\n".join(f"Detail line {index}" for index in range(45))
    terminal.actions = iter(["enter", *(["right"] * 30), "left", "down", "cancel"])

    tool_browser.browse_tools([_entry("long_tool", description), _entry("next_tool")])

    frames = list(map(_plain, terminal.frames))
    assert terminal.frames[1][-3] == ""
    displayed = "\n".join(frames)
    for index in range(45):
        assert f"Detail line {index}\n" in displayed
    assert "Detail line 44" in frames[-3]
    assert "Detail line 44" not in frames[-2]
    assert "› next_tool" in frames[-1]
    assert "query: string" not in frames[-1]


def test_expanded_details_use_spare_height_and_only_page_when_they_overflow(
    terminal: SimpleNamespace,
) -> None:
    def _shrink() -> str:
        terminal.lines = 24
        return "ignore"

    def _grow() -> str:
        terminal.lines = 60
        return "ignore"

    terminal.lines = 60
    terminal.actions = iter(["enter", _shrink, "right", _grow, "cancel"])
    description = "\n".join(f"Description line {i}" for i in range(12))
    entries = [_entry(f"tool_{i}", description) for i in range(30)]

    tool_browser.browse_tools(entries)

    initial, expanded, small, paged, grown = map(_plain, terminal.frames)
    assert "Description line 11" not in initial
    for frame in (expanded, grown):
        assert "Description line 11" in frame
        assert "query: string (required)" in frame
        assert "←→" not in frame
    assert "←→ 1/" in small and "←→ 2/" in paged
    assert "page · Details" not in small
    assert len(terminal.frames[1]) > len(terminal.frames[0])
    for frame, height in zip(terminal.frames, (60, 60, 24, 24, 60), strict=True):
        assert len(frame) < height


@pytest.mark.parametrize(("columns", "lines"), [(80, 24), (40, 12), (26, 8), (12, 5)])
def test_frames_fit_the_terminal_even_when_expanded(
    terminal: SimpleNamespace, columns: int, lines: int
) -> None:
    terminal.columns, terminal.lines = columns, lines
    terminal.actions = iter(["enter", "right", "down", "cancel"])

    tool_browser.browse_tools([_entry(f"tool_{i}", "Long description. " * 20) for i in range(30)])

    for frame in terminal.frames:
        assert len(frame) < lines
        assert all(prompt_text_width(Text.from_ansi(row).plain) < columns for row in frame)
    assert len(terminal.frames) == 4
    if columns >= 26:
        assert all("Esc" in _plain(frame) for frame in terminal.frames)


def test_scrolling_and_resize_keep_selection_visible_and_pages_valid(
    terminal: SimpleNamespace,
) -> None:
    def _shrink() -> str:
        terminal.columns, terminal.lines = 40, 10
        return "ignore"

    def _grow() -> str:
        terminal.columns, terminal.lines = 100, 30
        return "ignore"

    terminal.actions = iter(["up", "enter", *(["right"] * 20), _shrink, _grow, "cancel"])
    entries = [_entry(f"tool_{i}", "Description\n" * 45) for i in range(30)]

    tool_browser.browse_tools(entries)

    assert "› tool_29" in _plain(terminal.frames[1])
    for frame in terminal.frames[2:]:
        assert "▾ tool_29" in _plain(frame)
    assert len(terminal.frames[-2]) < 10
    assert "←→" in _plain(terminal.frames[-1])


def test_untrusted_metadata_is_literal_and_wide_text_remains_readable(
    terminal: SimpleNamespace,
) -> None:
    terminal.columns = 40
    terminal.actions = iter(["enter", *(["right"] * 20), "cancel"])
    entry = _entry("\x1b[31m危険_tool", "[bold]literal[/bold]\nUnicode: 界界界\n\x1b[2JLast line")

    tool_browser.browse_tools([entry])

    raw = "\n".join(row for frame in terminal.frames for row in frame)
    plain = "\n".join(map(_plain, terminal.frames))
    assert "\x1b[31m" not in raw and "\x1b[2J" not in raw
    assert "[bold]literal[/bold]" in plain and "界界界" in plain and "Last line" in plain
    assert all(
        prompt_text_width(Text.from_ansi(row).plain) < 40
        for frame in terminal.frames
        for row in frame
    )


def test_exception_restores_terminal(terminal: SimpleNamespace) -> None:
    def _fail() -> str:
        raise RuntimeError("input failed")

    terminal.actions = iter([_fail])
    with pytest.raises(RuntimeError, match="input failed"):
        tool_browser.browse_tools([_entry("tool")])

    assert terminal.events[-2:] == ["restore", "leave"]


@pytest.mark.parametrize("dismiss_on_resize", [False, True])
def test_resizing_keeps_browser_writes_inside_a_temporary_screen(
    terminal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, dismiss_on_resize: bool
) -> None:
    stream = io.StringIO()
    output = Vt100_Output(stream, lambda: Size(rows=terminal.lines, columns=terminal.columns))
    monkeypatch.setattr(tool_browser, "create_output", lambda: output)

    def _shrink() -> str:
        terminal.columns, terminal.lines = 40, 12
        return "cancel" if dismiss_on_resize else "down"

    terminal.actions = iter(["enter", _shrink, "cancel"])
    tool_browser.browse_tools([_entry("search_github"), _entry("fleet_scan")])

    written = stream.getvalue()
    enter, leave = "\x1b[?1049h", "\x1b[?1049l"
    assert written.startswith(enter) and written.endswith(leave)
    assert written.count(enter) == written.count(leave) == 1
    assert "search_github" in written and "query: string" in written
    assert "\x1b[3J" not in written  # Never clear the conversation's scrollback.
    assert terminal.events[-1] == "leave"


def test_details_preserve_parameter_requirements_and_full_clipped_name(
    terminal: SimpleNamespace,
) -> None:
    entry = ToolCatalogEntry(
        "tool_with_a_name_that_cannot_fit_in_a_narrow_list",
        ("chat",),
        "Description.",
        "",
        "query: string, limit?: integer",
        (ToolParameter("query", "string", True), ToolParameter("limit", "integer", False)),
    )
    terminal.columns = 40
    terminal.actions = iter(["enter", *(["right"] * 10), "cancel"])

    tool_browser.browse_tools([entry])

    output = "\n".join(map(_plain, terminal.frames))
    assert "query: string (required)" in output
    assert "limit: integer (optional)" in output
    details = [Text.from_ansi(row).plain.strip() for row in tool_browser._detail_lines(entry, 39)]
    assert details[0] == "name"
    assert "".join(details[1 : details.index("surfaces")]) == entry.name


def test_redirected_console_keeps_readable_output_even_with_tty_stdin(
    terminal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools_cmds, "build_tool_catalog", lambda: [_entry("search_github")])
    monkeypatch.setattr(tools_cmds, "repl_tty_interactive", lambda: True)
    output = io.StringIO()

    assert tools_cmds._cmd_tools(Session(), Console(file=output), []) is True

    assert "search_github" in output.getvalue() and "Find matching code." in output.getvalue()
    assert terminal.events == []


def test_headless_dispatch_does_not_open_browser_on_an_inherited_tty(
    terminal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools_cmds, "build_tool_catalog", lambda: [_entry("search_github")])
    monkeypatch.setattr(tools_cmds, "repl_tty_interactive", lambda: True)
    output = io.StringIO()

    assert (
        dispatch_slash("/tools", Session(), Console(file=output, force_terminal=True), is_tty=False)
        is True
    )

    assert terminal.events == []
    assert "search_github" in output.getvalue()


def test_live_interactive_dispatch_overrides_disabled_startup_preference(
    terminal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(OPENSRE_INTERACTIVE_ENV, "0")
    monkeypatch.setattr(tools_cmds, "build_tool_catalog", lambda: [_entry("search_github")])
    monkeypatch.setattr(tools_cmds, "repl_tty_interactive", lambda: True)
    output = io.StringIO()

    assert (
        dispatch_slash("/tools", Session(), Console(file=output, force_terminal=True), is_tty=True)
        is True
    )

    assert terminal.frames and "search_github" in _plain(terminal.frames[0])
    assert output.getvalue() == ""


@pytest.mark.parametrize("entries", [[], [_entry("tool")]])
def test_noninteractive_browser_never_reads_input(
    terminal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, entries: list[ToolCatalogEntry]
) -> None:
    monkeypatch.setattr(tool_browser, "repl_tty_interactive", lambda: False)

    tool_browser.browse_tools(entries)

    assert terminal.events == []
