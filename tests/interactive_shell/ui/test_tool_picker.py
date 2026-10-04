"""Arrow selection and terminal cleanup for the tool browser."""

from __future__ import annotations

from os import terminal_size

import pytest
from rich.text import Text

from surfaces.interactive_shell.ui import tool_picker
from surfaces.shared.terminal.tables.tool_catalog import ToolCatalogEntry


def test_enter_expands_details_and_arrows_switch_tools_without_exiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = [
        ToolCatalogEntry("first", ("chat",), "First " * 80, "", "(no params)"),
        ToolCatalogEntry("second", ("action",), "Second", "", "id: string"),
    ]
    events: list[str] = []
    actions = iter(("enter", "enter", "down", "cancel"))
    drawn: list[tuple[int, int, int]] = []
    monkeypatch.setattr(tool_picker, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(tool_picker, "menu_columns", lambda: 40)
    monkeypatch.setattr(
        tool_picker.shutil,
        "get_terminal_size",
        lambda **_kwargs: terminal_size((80, 24)),
    )
    monkeypatch.setattr(tool_picker, "enter_inline_menu", lambda: events.append("enter"))
    monkeypatch.setattr(tool_picker, "leave_inline_menu", lambda: events.append("leave"))
    monkeypatch.setattr(
        tool_picker,
        "erase_menu_lines",
        lambda _height, *, delete=False: events.append("erase" if delete else "redraw"),
    )
    monkeypatch.setattr(tool_picker, "read_menu_action", lambda: next(actions))

    def _draw(_items: object, **kwargs: int) -> int:
        drawn.append((kwargs["selected"], kwargs["detail_rows"], kwargs["detail_page"]))
        return 6

    monkeypatch.setattr(tool_picker, "_draw", _draw)

    tool_picker.browse_tools(entries)
    assert drawn == [(0, 0, 0), (0, 9, 0), (0, 9, 1), (1, 9, 0)]
    assert events == ["enter", "erase", "leave"]


def test_picker_rows_show_names_without_long_descriptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows: list[str] = []
    entry = ToolCatalogEntry(
        "\x1b[31mtool_name", ("chat",), "A long description " * 30, "", "x: string"
    )
    monkeypatch.setattr(tool_picker, "menu_columns", lambda: 24)
    monkeypatch.setattr(tool_picker, "write_menu_line", lambda row="": rows.append(row))

    assert (
        tool_picker._draw(
            [entry],
            selected=0,
            top=0,
            visible_rows=1,
            detail_rows=0,
            detail_page=0,
            erase_lines=0,
        )
        == 5
    )
    visible = "\n".join(Text.from_ansi(row).plain for row in rows)
    assert "tool_name" in visible
    assert "A long description" not in visible
    assert "\x1b[31m" not in "".join(rows)


def test_details_show_all_fields_across_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    rows: list[str] = []
    entry = ToolCatalogEntry("tool", ("chat", "action"), "Description " * 15, "", "id: string")
    monkeypatch.setattr(tool_picker, "menu_columns", lambda: 40)
    monkeypatch.setattr(tool_picker, "write_menu_line", lambda row="": rows.append(row))

    details = tool_picker._detail_lines(entry, 40)
    pages = (len(details) + 2) // 3
    for page in range(pages):
        tool_picker._draw(
            [entry],
            selected=0,
            top=0,
            visible_rows=1,
            detail_rows=3,
            detail_page=page,
            erase_lines=0,
        )
    visible = "\n".join(Text.from_ansi(row).plain for row in rows)
    assert "surfaces    chat, action" in visible
    assert "params      id: string" in visible
    assert "description Description" in visible
    assert "".join(line[12:] for line in details[2:]) == entry.description


def test_expanded_marker_and_spacing_match_content(monkeypatch: pytest.MonkeyPatch) -> None:
    rows: list[str] = []
    entry = ToolCatalogEntry("tool", ("chat",), "Short description", "", "id: string")
    monkeypatch.setattr(tool_picker, "menu_columns", lambda: 50)
    monkeypatch.setattr(tool_picker, "write_menu_line", lambda row="": rows.append(row))

    height = tool_picker._draw(
        [entry],
        selected=0,
        top=0,
        visible_rows=1,
        detail_rows=9,
        detail_page=0,
        erase_lines=0,
    )
    visible = [Text.from_ansi(row).plain for row in rows]
    assert height == len(rows) == 10
    assert "▾  tool" in visible[3]
    assert visible[4] == ""
    assert visible[8] == ""


def test_multiline_description_keeps_blank_lines_and_headings() -> None:
    entry = ToolCatalogEntry("tool", ("chat",), "Intro\n\n## Heading\n\tstep", "", "(no params)")

    details = tool_picker._detail_lines(entry, 50)

    assert [line[12:] for line in details[2:]] == ["Intro", "", "## Heading", " step"]


def test_narrow_terminal_keeps_description_label_and_value() -> None:
    entry = ToolCatalogEntry("tool", ("chat",), "First words", "", "id: string")

    details = tool_picker._detail_lines(entry, 15)

    assert "description" in details
    assert "First words" in details
    assert all(prompt_width <= 11 for prompt_width in map(tool_picker.prompt_text_width, details))


def test_resizing_clamps_open_detail_page(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = ToolCatalogEntry("tool", ("chat",), "Description " * 4, "", "id: string")
    actions = iter(("enter", "enter", "cancel"))
    terminal_height = 10
    pages: list[tuple[int, int]] = []

    def _read_action() -> str:
        nonlocal terminal_height
        action = next(actions)
        if action == "enter" and pages[-1] == (2, 0):
            terminal_height = 30
        return action

    monkeypatch.setattr(tool_picker, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(tool_picker, "menu_columns", lambda: 40)
    monkeypatch.setattr(
        tool_picker.shutil,
        "get_terminal_size",
        lambda **_kwargs: terminal_size((80, terminal_height)),
    )
    monkeypatch.setattr(tool_picker, "enter_inline_menu", lambda: None)
    monkeypatch.setattr(tool_picker, "leave_inline_menu", lambda: None)
    monkeypatch.setattr(tool_picker, "erase_menu_lines", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_picker, "read_menu_action", _read_action)

    def _draw(_items: object, **kwargs: int) -> int:
        pages.append((kwargs["detail_rows"], kwargs["detail_page"]))
        return 6

    monkeypatch.setattr(tool_picker, "_draw", _draw)

    tool_picker.browse_tools([entry])
    assert pages == [(0, 0), (2, 0), (10, 0)]
