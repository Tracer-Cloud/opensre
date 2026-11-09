"""Tests for the full-screen /help command browser and its printed fallbacks."""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest

from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.ui.help import help_browser

_ANSI_RE = re.compile(r"\x1b\[[0-9;:]*[A-Za-z]")


def _plain(rows: tuple[str, ...]) -> list[str]:
    return [_ANSI_RE.sub("", row).rstrip() for row in rows]


def _cmd(
    name: str,
    description: str | None = None,
    *,
    usage: tuple[str, ...] = (),
    examples: tuple[str, ...] = (),
) -> SlashCommand:
    return SlashCommand(
        name,
        description or f"{name} description",
        lambda *_args: True,
        usage=usage,
        examples=examples,
    )


def _sections(count: int = 2) -> list[tuple[str, list[SlashCommand]]]:
    return [
        ("Quick Access", [_cmd("/model"), _cmd("/status")]),
        ("Tasks", [_cmd(f"/task{index}") for index in range(count)]),
    ]


def _entries(sections: list[tuple[str, list[SlashCommand]]]) -> list[help_browser._Entry]:
    return help_browser._flatten(sections)


class TestFrame:
    """The frame must fit the terminal exactly; the old inline menu did not."""

    @pytest.mark.parametrize("height", [12, 14, 20, 24, 40, 60])
    def test_frame_never_exceeds_the_terminal_height(self, height: int) -> None:
        # A frame taller than the screen is what scrolled the old picker's
        # header off and left its rows stranded above the next command's output.
        frame = help_browser._render_frame(
            _entries(_sections(40)),
            selected=0,
            top=0,
            page=0,
            query="",
            width=100,
            height=height,
        )

        assert len(frame.rows) <= height - 1

    def test_tiny_terminal_renders_a_resize_notice_instead_of_a_clipped_frame(self) -> None:
        frame = help_browser._render_frame(
            _entries(_sections()),
            selected=0,
            top=0,
            page=0,
            query="",
            width=20,
            height=8,
        )

        assert "Resize to at least" in " ".join(_plain(frame.rows))

    def test_narrow_frame_drops_the_purpose_column_rather_than_wrapping(self) -> None:
        frame = help_browser._render_frame(
            _entries([("Quick Access", [_cmd("/model", "Show or change active LLM settings.")])]),
            selected=0,
            top=0,
            page=0,
            query="",
            width=40,
            height=20,
        )

        rendered = _plain(frame.rows)
        assert any("/model" in row for row in rendered)
        assert not any("Show or change active LLM" in row for row in rendered[:6])

    def test_category_labels_group_the_unfiltered_list(self) -> None:
        frame = help_browser._render_frame(
            _entries(_sections()), selected=0, top=0, page=0, query="", width=100, height=30
        )

        rendered = _plain(frame.rows)
        assert "  Quick Access" in rendered
        assert "  Tasks" in rendered

    def test_preview_shows_the_selected_commands_category_and_usage(self) -> None:
        command = _cmd("/model", "Change the model.", usage=("/model set <name>",))
        frame = help_browser._render_frame(
            _entries([("Models", [command])]),
            selected=0,
            top=0,
            page=0,
            query="",
            width=100,
            height=30,
        )

        rendered = " ".join(_plain(frame.rows))
        assert "Models › /model" in rendered
        assert "/model set <name>" in rendered

    def test_selected_row_uses_the_active_theme_selection_style(self) -> None:
        frame = help_browser._render_frame(
            _entries(_sections()), selected=1, top=0, page=0, query="", width=100, height=30
        )

        selected_rows = [row for row in frame.rows if ui_theme.MENU_SELECTION_ROW_ANSI in row]
        assert len(selected_rows) == 1
        assert "/status" in _ANSI_RE.sub("", selected_rows[0])


class TestHints:
    @pytest.mark.parametrize("width", [40, 55, 80, 120])
    def test_a_multi_page_preview_advertises_paging_at_every_width(self, width: int) -> None:
        # The narrow layout used to drop ←→ entirely, hiding usage, examples
        # and notes past the first page with nothing to say they existed.
        hint = _ANSI_RE.sub(
            "",
            help_browser._hint_row(query="", position="1/9", page=0, pages=3, width=width),
        )

        assert "←→" in hint
        assert "1/3" in hint

    def test_a_single_page_preview_shows_no_paging_control(self) -> None:
        hint = _ANSI_RE.sub(
            "",
            help_browser._hint_row(query="", position="1/9", page=0, pages=1, width=40),
        )

        assert "←→" not in hint


class TestWindow:
    def test_window_scrolls_the_minimum_needed_to_reveal_the_selection(self) -> None:
        lines, line_of = help_browser._lines(_entries(_sections(40)), grouped=True)

        assert help_browser._window_top(lines, line_of[0], 10, 0) == 0
        # Selecting past the window bottom scrolls by exactly the overshoot.
        top = help_browser._window_top(lines, line_of[20], 10, 0)
        assert top == line_of[20] - 9
        # A selection already inside the window does not move it.
        assert help_browser._window_top(lines, line_of[18], 10, top) == top
        # Scrolling back above the window stops at the selection, not at the list start.
        assert help_browser._window_top(lines, line_of[5], 10, top) == line_of[5]

    def test_window_keeps_the_category_label_above_the_first_visible_command(self) -> None:
        lines, line_of = help_browser._lines(_entries(_sections(40)), grouped=True)
        # /task0 sits directly under the "Tasks" label.
        selected_line = line_of[2]

        top = help_browser._window_top(lines, selected_line, 10, selected_line)

        assert lines[top].is_label
        assert lines[top].section == "Tasks"


class TestFilter:
    def test_filter_matches_name_purpose_and_category(self) -> None:
        sections = [
            ("Quick Access", [_cmd("/model", "Show or change active LLM settings.")]),
            ("Tasks", [_cmd("/work", "Track durable work items.")]),
        ]
        entries = _entries(sections)

        assert [e.command.name for e in help_browser._filtered(entries, "mod")] == ["/model"]
        assert [e.command.name for e in help_browser._filtered(entries, "durable")] == ["/work"]
        assert [e.command.name for e in help_browser._filtered(entries, "tasks")] == ["/work"]
        assert len(help_browser._filtered(entries, "")) == 2

    def test_name_matches_outrank_purpose_matches(self) -> None:
        entries = _entries(
            [
                ("Session", [_cmd("/privacy", "Show redaction and status.")]),
                ("Quick Access", [_cmd("/status", "Show session status.")]),
            ]
        )

        assert [e.command.name for e in help_browser._filtered(entries, "stat")] == [
            "/status",
            "/privacy",
        ]

    def test_filter_lists_a_quick_access_duplicate_once(self) -> None:
        # /status is listed under Quick Access *and* its canonical category;
        # flattened by a filter the repeat would read as a duplicate row.
        status = _cmd("/status")
        entries = _entries([("Quick Access", [status]), ("Session", [status])])

        assert [e.section for e in help_browser._filtered(entries, "status")] == ["Quick Access"]

    def test_filtered_list_is_flat_so_no_label_row_can_be_selected(self) -> None:
        lines, line_of = help_browser._lines(_entries(_sections()), grouped=False)

        assert not any(line.is_label for line in lines)
        assert line_of == [0, 1, 2, 3]

    def test_empty_filter_result_renders_a_notice_and_a_zero_counter(self) -> None:
        frame = help_browser._render_frame(
            [], selected=0, top=0, page=0, query="zzz", width=100, height=30
        )

        rendered = " ".join(_plain(frame.rows))
        assert "No command matches that filter." in rendered
        assert "0 matches" in rendered


def _drive(keys: list[str], sections: list[tuple[str, list[SlashCommand]]]) -> str | None:
    """Run the browser against a scripted key stream with terminal I/O stubbed."""

    class _FakeOutput:
        def __getattr__(self, _name: str) -> object:
            return lambda *_args, **_kwargs: None

    pressed: Iterator[str] = iter(keys)

    def _read(**_kwargs: object) -> str:
        return next(pressed)

    import surfaces.interactive_shell.ui.help.help_browser as module

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(module, "repl_tty_interactive", lambda: True)
        patch.setattr(module, "create_output", lambda: _FakeOutput())
        patch.setattr(module, "enter_inline_menu", lambda: None)
        patch.setattr(module, "leave_inline_menu", lambda: None)
        patch.setattr(module, "read_menu_or_char", _read)
        return module.browse_help_commands(sections)


class TestInteraction:
    def test_enter_returns_the_focused_command(self) -> None:
        assert _drive(["down", "enter"], _sections()) == "/status"

    def test_typing_filters_then_enter_runs_the_match(self) -> None:
        sections = [
            ("Quick Access", [_cmd("/model"), _cmd("/status")]),
            ("Tasks", [_cmd("/work")]),
        ]

        assert _drive(["w", "o", "enter"], sections) == "/work"

    def test_esc_clears_a_live_filter_before_it_closes_the_browser(self) -> None:
        # One Esc must not discard the whole browser while a filter is up:
        # it clears the filter, and only a second Esc closes.
        assert _drive(["s", "cancel", "enter"], _sections()) == "/model"
        assert _drive(["s", "cancel", "cancel"], _sections()) is None

    def test_backspace_restores_entries_dropped_by_the_filter(self) -> None:
        assert _drive(["s", "t", "backspace", "backspace", "down", "enter"], _sections()) == (
            "/status"
        )

    def test_enter_on_an_empty_filter_result_closes_without_a_command(self) -> None:
        assert _drive(["z", "z", "enter"], _sections()) is None

    def test_leaving_the_browser_always_restores_the_terminal(self) -> None:
        import surfaces.interactive_shell.ui.help.help_browser as module

        class _FakeOutput:
            def __getattr__(self, _name: str) -> object:
                return lambda *_args, **_kwargs: None

        left: list[str] = []
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(module, "repl_tty_interactive", lambda: True)
            patch.setattr(module, "create_output", lambda: _FakeOutput())
            patch.setattr(module, "enter_inline_menu", lambda: left.append("enter"))
            patch.setattr(module, "leave_inline_menu", lambda: left.append("leave"))

            def _boom(**_kwargs: object) -> str:
                raise KeyboardInterrupt

            patch.setattr(module, "read_menu_or_char", _boom)
            with pytest.raises(KeyboardInterrupt):
                module.browse_help_commands(_sections())

        assert left == ["enter", "leave"]
