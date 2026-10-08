"""Tests for the grouped /help picker renderer."""

from __future__ import annotations

import io
import re
import sys

from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.ui.help import help_menu

_ANSI_RE = re.compile(r"\x1b\[[0-9;:]*[A-Za-z]")


def _cmd(name: str, description: str | None = None) -> SlashCommand:
    return SlashCommand(name, description or f"{name} description", lambda *_args: True)


def test_flatten_help_rows_preserves_category_headers() -> None:
    rows = help_menu._flatten_help_rows(
        [
            ("Session", [_cmd("/status")]),
            ("Tasks", [_cmd("/tasks"), _cmd("/cancel")]),
        ]
    )

    assert [row.section for row in rows if row.section is not None] == ["Session", "Tasks"]
    assert sum(1 for row in rows if row.separator) == 1
    assert [row.command.name for row in rows if row.command is not None] == [
        "/status",
        "/tasks",
        "/cancel",
    ]


def test_navigation_skips_category_headers() -> None:
    rows = help_menu._flatten_help_rows(
        [
            ("Session", [_cmd("/status")]),
            ("Tasks", [_cmd("/tasks")]),
        ]
    )

    assert help_menu._first_selectable_index(rows) == 1
    assert help_menu._next_selectable_index(rows, 1, 1) == 4
    assert help_menu._next_selectable_index(rows, 4, -1) == 1


def test_draw_help_menu_renders_horizontal_category_dividers(monkeypatch) -> None:
    rows = help_menu._flatten_help_rows(
        [
            ("Session", [_cmd("/status")]),
            ("Tasks", [_cmd("/tasks")]),
        ]
    )
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(help_menu, "menu_columns", lambda: 40)

    help_menu._draw_help_menu(
        rows,
        selected=1,
        expanded=None,
        erase_lines=0,
        viewport_height=7,
    )

    plain = _ANSI_RE.sub("", out.getvalue())
    assert "Session" in plain
    assert "Tasks" in plain
    assert plain.count(help_menu._separator_rule(40)) >= 2
    assert "┼" in plain
    assert help_menu._render_grid_row("Session", "", 40) in plain
    assert help_menu._render_grid_row("Tasks", "", 40) in plain


def test_section_rows_keep_divider_dim() -> None:
    rendered = help_menu._render_help_row(
        help_menu.HelpRow(section="Investigation"),
        selected=False,
        expanded=False,
        width=40,
    )

    assert f"{ui_theme.BOLD_BRAND_ANSI}Investigation" in rendered
    assert f"{ui_theme.DIM_COUNTER_ANSI}│" in rendered


def test_detail_rows_use_text_labels_dim_values_and_dim_divider() -> None:
    label = help_menu._render_display_row(
        help_menu.HelpDisplayRow(detail=help_menu.HelpDetailLine("usage:", "label")),
        selected=False,
        expanded=False,
        width=40,
    )
    value = help_menu._render_display_row(
        help_menu.HelpDisplayRow(detail=help_menu.HelpDetailLine("  /help", "value")),
        selected=False,
        expanded=False,
        width=40,
    )

    assert f"{ui_theme.DIM_COUNTER_ANSI}{' ' * help_menu._left_column_width(40)}│ " in label
    assert f"{ui_theme.TEXT_ANSI}usage:" in label
    assert f"{ui_theme.DIM_COUNTER_ANSI}{' ' * help_menu._left_column_width(40)}│ " in value
    assert f"{ui_theme.DIM_COUNTER_ANSI}  /help" in value


def test_draw_help_menu_centers_title(monkeypatch) -> None:
    rows = help_menu._flatten_help_rows([("Session", [_cmd("/status")])])
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(help_menu, "menu_columns", lambda: 40)

    help_menu._draw_help_menu(
        rows,
        selected=1,
        expanded=None,
        erase_lines=0,
        viewport_height=7,
    )

    plain_lines = _ANSI_RE.sub("", out.getvalue()).splitlines()
    assert "             Slash commands             " in plain_lines


def test_draw_help_menu_uses_bounded_viewport_and_category_context(monkeypatch) -> None:
    sections = [
        ("Session", [_cmd(f"/session-{index}") for index in range(10)]),
        ("Tasks", [_cmd(f"/task-{index}") for index in range(10)]),
    ]
    rows = help_menu._flatten_help_rows(sections)
    selected = next(
        index for index, row in enumerate(rows) if row.command and row.command.name == "/task-2"
    )
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(help_menu, "menu_columns", lambda: 90)

    height = help_menu._draw_help_menu(
        rows,
        selected=selected,
        expanded=None,
        erase_lines=0,
        viewport_height=7,
    )

    rendered = out.getvalue()
    plain = _ANSI_RE.sub("", rendered)
    assert height == 13
    assert "Tasks" in plain
    assert "/task-2" in plain
    assert "/session-0" not in plain
    assert "/task-9" not in plain


def test_draw_help_menu_expands_selected_command_inline(monkeypatch) -> None:
    command = SlashCommand(
        "/new",
        "Start a new session keeping conversation context.",
        lambda *_args: True,
        usage=("/new",),
        notes=("Unlike /clear, /new preserves the LLM conversation context.",),
    )
    rows = help_menu._flatten_help_rows([("Session", [command])])
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(help_menu, "menu_columns", lambda: 90)

    help_menu._draw_help_menu(
        rows,
        selected=1,
        expanded=1,
        erase_lines=0,
        viewport_height=7,
    )

    plain = _ANSI_RE.sub("", out.getvalue())
    assert "/new" in plain
    assert "> ▾ /new" in plain
    assert "│ Start a new session" in plain
    assert help_menu._render_grid_row("", "usage:", 90) in plain
    assert "usage:" in plain
    assert "LLM conversation context" in plain


def test_draw_help_menu_marks_expandable_and_plain_commands(monkeypatch) -> None:
    expandable = SlashCommand(
        "/trust",
        "Manage trust mode.",
        lambda *_args: True,
        usage=("/trust on", "/trust off"),
    )
    plain_command = _cmd("/status", "Show session status.")
    rows = help_menu._flatten_help_rows([("Session", [expandable, plain_command])])
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(help_menu, "menu_columns", lambda: 90)

    help_menu._draw_help_menu(
        rows,
        selected=1,
        expanded=None,
        erase_lines=0,
        viewport_height=7,
    )

    plain = _ANSI_RE.sub("", out.getvalue())
    assert "> ▸ /trust" in plain
    assert "│ Manage trust mode." in plain
    assert "  /status" in plain
    assert "▸ /status" not in plain


def test_command_rows_highlight_only_unselected_command_name() -> None:
    rendered = help_menu._render_command_row(
        SlashCommand(
            "/trust",
            "Manage trust mode.",
            lambda *_args: True,
            usage=("/trust on", "/trust off"),
        ),
        selected=False,
        expanded=False,
        width=60,
    )

    assert (
        f"{ui_theme.DIM_COUNTER_ANSI}   ▸ {ui_theme.ANSI_RESET}{ui_theme.HIGHLIGHT_ANSI}/trust"
    ) in rendered
    assert f"{ui_theme.HIGHLIGHT_ANSI}▸" not in rendered


def test_help_menu_command_rows_track_active_theme() -> None:
    from infrastructure.terminal.theme import set_active_theme

    set_active_theme("green")
    green_highlight = ui_theme.HIGHLIGHT_ANSI
    set_active_theme("purple")
    rendered = help_menu._render_command_row(
        SlashCommand("/trust", "Manage trust mode.", lambda *_args: True),
        selected=False,
        expanded=False,
        width=60,
    )

    assert ui_theme.HIGHLIGHT_ANSI in rendered
    assert green_highlight not in rendered


def test_expanded_detail_lines_include_all_usage_examples_and_notes() -> None:
    command = SlashCommand(
        "/model",
        "Show model settings.",
        lambda *_args: True,
        usage=tuple(f"/model action {index}" for index in range(10)),
        examples=("/model set openai",),
        notes=("Provider defaults can be restored.",),
    )

    lines = help_menu._expanded_detail_lines(command)
    text_lines = [line.text for line in lines]

    assert lines[0].role == "label"
    assert lines[1].role == "value"
    assert "/model action 0" in lines[1].text
    assert "  /model action 9" in text_lines
    assert "examples:" in text_lines
    assert "  /model set openai" in text_lines
    assert "notes:" in text_lines
    assert "  Provider defaults can be restored." in text_lines
    assert "…" not in text_lines


def test_draw_help_menu_expands_viewport_to_show_all_details(monkeypatch) -> None:
    command = SlashCommand(
        "/model",
        "Show model settings.",
        lambda *_args: True,
        usage=tuple(f"/model action {index}" for index in range(10)),
    )
    rows = help_menu._flatten_help_rows([("Models", [command, _cmd("/status")])])
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(help_menu, "menu_columns", lambda: 90)

    height = help_menu._draw_help_menu(
        rows,
        selected=1,
        expanded=1,
        erase_lines=0,
        viewport_height=7,
    )

    plain = _ANSI_RE.sub("", out.getvalue())
    assert height > 13
    assert "/model action 0" in plain
    assert "/model action 9" in plain
    assert "…" not in plain


def test_choose_help_command_space_toggles_inline_details_and_exits(monkeypatch) -> None:
    sections = [
        (
            "Session",
            [
                _cmd("/status"),
                SlashCommand(
                    "/trust",
                    "Manage trust mode.",
                    lambda *_args: True,
                    usage=("/trust on", "/trust off"),
                ),
            ],
        )
    ]
    out = io.StringIO()
    actions = iter(["down", "space", "cancel"])
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(help_menu, "menu_columns", lambda: 80)
    monkeypatch.setattr(help_menu, "read_menu_action", lambda **_kwargs: next(actions))

    selected = help_menu.choose_help_command(sections)

    assert selected is None
    rendered = out.getvalue()
    assert "\x1b[" in rendered
    # Redraw clears in place; cancel deletes the leftover rows.
    assert "A\r\x1b[J" in rendered
    assert "M" in rendered
    plain = _ANSI_RE.sub("", rendered)
    assert "/trust on" in plain


def test_choose_help_command_enter_selects_command_without_details(monkeypatch) -> None:
    sections = [("Session", [_cmd("/status")])]
    out = io.StringIO()
    actions = iter(["enter"])
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(help_menu, "menu_columns", lambda: 80)
    monkeypatch.setattr(help_menu, "read_menu_action", lambda **_kwargs: next(actions))

    assert help_menu.choose_help_command(sections) == "/status"

    plain = _ANSI_RE.sub("", out.getvalue())
    assert "No additional usage." not in plain


def test_choose_help_command_ignores_unknown_actions(monkeypatch) -> None:
    sections = [("Session", [_cmd("/status")])]
    out = io.StringIO()
    actions = iter(["ignore", "cancel"])
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(help_menu, "menu_columns", lambda: 80)
    monkeypatch.setattr(help_menu, "read_menu_action", lambda **_kwargs: next(actions))

    assert help_menu.choose_help_command(sections) is None

    rendered = out.getvalue()
    assert rendered.count("Slash commands") == 2


def test_draw_help_menu_fits_a_short_terminal_and_keeps_the_selected_row_visible(
    monkeypatch,
) -> None:
    rows = help_menu._flatten_help_rows(
        [("Commands", [_cmd(f"/command-{index}") for index in range(24)])]
    )
    selected = next(
        index for index, row in enumerate(rows) if row.command and row.command.name == "/command-12"
    )
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("COLUMNS", "40")
    monkeypatch.setenv("LINES", "20")

    height = help_menu._draw_help_menu(
        rows,
        selected=selected,
        expanded=None,
        erase_lines=0,
    )

    plain = _ANSI_RE.sub("", out.getvalue())
    assert height <= 19
    assert "Slash commands" in plain
    assert "13/24" in plain
    assert ">   /command-12" in plain


def test_draw_help_menu_keeps_expanded_details_within_a_short_terminal(
    monkeypatch,
) -> None:
    command = SlashCommand(
        "/model",
        "Configure models.",
        lambda *_args: True,
        usage=tuple(f"/model action {index}" for index in range(20)),
    )
    rows = help_menu._flatten_help_rows([("Models", [command])])
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("COLUMNS", "40")
    monkeypatch.setenv("LINES", "20")

    height = help_menu._draw_help_menu(rows, selected=1, expanded=1, erase_lines=0)

    plain = _ANSI_RE.sub("", out.getvalue())
    assert height <= 19
    assert "Slash commands" in plain
    assert "> ▾ /model" in plain
    assert "/model action 0" in plain
    assert "/model action 19" not in plain


def test_draw_help_menu_clips_its_hint_to_a_short_terminal_width(monkeypatch) -> None:
    rows = help_menu._flatten_help_rows([("Session", [_cmd("/status")])])
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("COLUMNS", "40")
    monkeypatch.setenv("LINES", "20")

    help_menu._draw_help_menu(rows, selected=1, expanded=None, erase_lines=0)

    plain_lines = _ANSI_RE.sub("", out.getvalue()).splitlines()
    assert len(plain_lines[-1]) <= 39


def test_choose_help_command_pages_oversized_expanded_details(monkeypatch) -> None:
    command = SlashCommand(
        "/loops",
        "Inspect loops.",
        lambda *_args: True,
        usage=tuple(f"/loops action {index}" for index in range(20)),
    )
    out = io.StringIO()
    actions = iter(["space", "right", "cancel"])
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "24")
    monkeypatch.setattr(
        help_menu,
        "read_menu_action",
        lambda **_kwargs: next(actions),
    )

    assert help_menu.choose_help_command([("Session", [command])]) is None

    plain = _ANSI_RE.sub("", out.getvalue())
    assert "/loops action 0" in plain
    assert "/loops action 19" in plain
