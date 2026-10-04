"""Behavioral coverage for the purpose-first, inline tool browser."""

from __future__ import annotations

import io
from os import terminal_size
from types import SimpleNamespace

import pytest
from rich.console import Console
from rich.text import Text

from surfaces.interactive_shell.command_registry import dispatch_slash, tools_cmds
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui import tool_browser
from surfaces.shared.terminal.prompt_layout import prompt_text_width
from surfaces.shared.terminal.tables.tool_catalog import ToolCatalogEntry, ToolParameter


def _entry(name: str, description: str = "Find matching code.") -> ToolCatalogEntry:
    return ToolCatalogEntry(name, ("chat", "action"), description, "", "query: string")


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

    def _erase(_height: int, *, delete: bool = False) -> None:
        events.append("delete" if delete else "redraw")
        rows.clear()

    def _size(**_kwargs: object) -> terminal_size:
        return terminal_size((state.columns, state.lines))

    monkeypatch.setattr(tool_browser, "get_terminal_size", _size)
    monkeypatch.setattr(tool_browser, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(tool_browser, "enter_inline_menu", lambda: events.append("enter"))
    monkeypatch.setattr(tool_browser, "leave_inline_menu", lambda: events.append("leave"))
    monkeypatch.setattr(tool_browser, "erase_menu_lines", _erase)
    monkeypatch.setattr(tool_browser, "write_menu_line", lambda row="": rows.append(row))
    monkeypatch.setattr(tool_browser, "read_menu_or_char", _read_key)
    state.frames, state.events = frames, events
    return state


def _plain(frame: list[str]) -> str:
    return "\n".join(Text.from_ansi(row).plain for row in frame)


def test_browse_shows_every_visible_tools_purpose_then_expands_in_place(
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
    assert "chat · action" in initial
    assert "parameters" not in initial
    assert "▾ search_github" in expanded and "parameters" in expanded
    assert "query: string" in expanded and "description" in expanded
    assert "parameters" not in collapsed
    assert "› fleet_scan" in moved and "▾ fleet_scan" in second
    assert output.getvalue() == ""
    assert terminal.events[0] == "enter"
    assert terminal.events[-2:] == ["delete", "leave"]


def test_long_details_can_be_read_forward_and_backward_without_changing_tool(
    terminal: SimpleNamespace,
) -> None:
    description = "\n".join(f"Detail line {index}" for index in range(45))
    terminal.actions = iter(["enter", *(["right"] * 30), "left", "down", "cancel"])

    tool_browser.browse_tools([_entry("long_tool", description), _entry("next_tool")])

    frames = list(map(_plain, terminal.frames))
    displayed = "\n".join(frames)
    for index in range(45):
        assert f"Detail line {index}\n" in displayed
    assert "Detail line 44" in frames[-3]
    assert "Detail line 44" not in frames[-2]
    assert "› next_tool" in frames[-1]
    assert "parameters" not in frames[-1]


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
        assert "description" in "\n".join(map(_plain, terminal.frames))
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
    assert "Details" in _plain(terminal.frames[-1])


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

    assert terminal.events[-2:] == ["delete", "leave"]


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


@pytest.mark.parametrize("entries", [[], [_entry("tool")]])
def test_noninteractive_browser_never_reads_input(
    terminal: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, entries: list[ToolCatalogEntry]
) -> None:
    monkeypatch.setattr(tool_browser, "repl_tty_interactive", lambda: False)

    tool_browser.browse_tools(entries)

    assert terminal.events == []
