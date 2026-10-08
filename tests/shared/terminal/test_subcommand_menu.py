"""Tests for the composer-tray-styled subcommand picker."""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest

from infrastructure.terminal import theme as ui_theme
from surfaces.shared.terminal.components import subcommand_menu

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_OPTIONS = (
    ("show", "show active provider and models"),
    ("set", "switch provider  ·  /model set <provider> [model]"),
    ("restore", "restore the active provider's default reasoning model"),
    ("toolcall", "manage toolcall model for the active provider"),
)


def _plain(row: str) -> str:
    return _ANSI_RE.sub("", row)


def _rows(width: int, *, selected: int = 0, more: bool = False) -> list[str]:
    return [
        subcommand_menu._rule("╭", "╮", width),
        subcommand_menu._header_row("/model", selected, len(_OPTIONS), width),
        subcommand_menu._framed("", ui_theme.INPUT_SURFACE_BG_ANSI, width),
        *(
            subcommand_menu._option_row(
                name, meta, selected=index == selected, name_width=8, width=width
            )
            for index, (name, meta) in enumerate(_OPTIONS)
        ),
        subcommand_menu._hint_row(more=more, width=width),
        subcommand_menu._rule("╰", "╯", width),
    ]


@pytest.mark.parametrize("width", [30, 44, 60, 80, 110, 200])
def test_every_painted_row_is_exactly_the_paint_width(width: int) -> None:
    # menu_columns() is already one column short of the TTY. A row even one
    # column wider puts a glyph in the last cell, and DEC autowrap then reflows
    # the menu on the next resize.
    assert {len(_plain(row)) for row in _rows(width, more=True)} == {width}


def test_long_metadata_is_clipped_not_wrapped() -> None:
    row = subcommand_menu._option_row(
        "set",
        "x" * 500,
        selected=False,
        name_width=8,
        width=60,
    )

    assert len(_plain(row)) == 60


def test_narrow_width_drops_the_description_column() -> None:
    row = _plain(
        subcommand_menu._option_row(
            "show", "show active provider and models", selected=False, name_width=8, width=40
        )
    )

    assert "show" in row
    assert "active provider" not in row


def test_focused_row_uses_the_trays_selection_plate() -> None:
    focused = subcommand_menu._option_row(
        "show", "a purpose", selected=True, name_width=8, width=80
    )
    resting = subcommand_menu._option_row(
        "set", "a purpose", selected=False, name_width=8, width=80
    )

    assert ui_theme.menu_selection_bg_ansi() in focused
    assert ui_theme.menu_selection_bg_ansi() not in resting
    assert ui_theme.INPUT_SURFACE_BG_ANSI in resting


def test_header_counts_the_focused_option() -> None:
    header = _plain(subcommand_menu._header_row("/model", 2, 4, 80))

    assert "Subcommands · /model" in header
    assert "3 / 4" in header


def test_hint_advertises_more_rows_only_when_the_list_is_scrolled() -> None:
    assert "↓ more" in _plain(subcommand_menu._hint_row(more=True, width=80))
    assert "↓ more" not in _plain(subcommand_menu._hint_row(more=False, width=80))


def _drive(keys: list[str], options: tuple[tuple[str, str], ...] = _OPTIONS) -> str | None:
    pressed: Iterator[str] = iter(keys)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(subcommand_menu, "repl_tty_interactive", lambda: True)
        patch.setattr(subcommand_menu, "enter_inline_menu", lambda: None)
        patch.setattr(subcommand_menu, "leave_inline_menu", lambda: None)
        patch.setattr(subcommand_menu, "erase_menu_lines", lambda *_a, **_k: None)
        patch.setattr(subcommand_menu, "write_menu_line", lambda *_a, **_k: None)
        patch.setattr(subcommand_menu, "menu_columns", lambda: 80)
        patch.setattr(subcommand_menu, "read_menu_action", lambda: next(pressed))
        return subcommand_menu.repl_choose_subcommand(parent="/model", options=options)


class TestInteraction:
    def test_enter_returns_the_focused_subcommand_name(self) -> None:
        assert _drive(["down", "enter"]) == "set"

    def test_esc_closes_without_a_selection(self) -> None:
        # The old numbered menu needed a "done" row to leave; Esc is the only
        # exit the tray advertises, so it must work.
        assert _drive(["cancel"]) is None

    def test_navigation_wraps_at_both_ends(self) -> None:
        assert _drive(["up", "enter"]) == "toolcall"

    def test_initial_value_opens_on_that_row(self) -> None:
        pressed: Iterator[str] = iter(["enter"])
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(subcommand_menu, "repl_tty_interactive", lambda: True)
            patch.setattr(subcommand_menu, "enter_inline_menu", lambda: None)
            patch.setattr(subcommand_menu, "leave_inline_menu", lambda: None)
            patch.setattr(subcommand_menu, "erase_menu_lines", lambda *_a, **_k: None)
            patch.setattr(subcommand_menu, "write_menu_line", lambda *_a, **_k: None)
            patch.setattr(subcommand_menu, "menu_columns", lambda: 80)
            patch.setattr(subcommand_menu, "read_menu_action", lambda: next(pressed))
            picked = subcommand_menu.repl_choose_subcommand(
                parent="/model", options=_OPTIONS, initial_value="restore"
            )

        assert picked == "restore"

    def test_leaving_always_restores_the_terminal(self) -> None:
        events: list[str] = []
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(subcommand_menu, "repl_tty_interactive", lambda: True)
            patch.setattr(subcommand_menu, "enter_inline_menu", lambda: events.append("enter"))
            patch.setattr(subcommand_menu, "leave_inline_menu", lambda: events.append("leave"))
            patch.setattr(subcommand_menu, "erase_menu_lines", lambda *_a, **_k: None)
            patch.setattr(subcommand_menu, "write_menu_line", lambda *_a, **_k: None)
            patch.setattr(subcommand_menu, "menu_columns", lambda: 80)

            def _boom() -> str:
                raise KeyboardInterrupt

            patch.setattr(subcommand_menu, "read_menu_action", _boom)
            with pytest.raises(KeyboardInterrupt):
                subcommand_menu.repl_choose_subcommand(parent="/model", options=_OPTIONS)

        assert events == ["enter", "leave"]


class TestCatalogIsShared:
    """The picker and the composer tray must not drift apart again."""

    @pytest.mark.parametrize(
        ("module_path", "catalog", "command"),
        [
            (
                "surfaces.interactive_shell.command_registry.model.command",
                "_MODEL_FIRST_ARGS",
                "/model",
            ),
            (
                "surfaces.interactive_shell.command_registry.settings_cmds",
                "_TRUST_FIRST_ARGS",
                "/trust",
            ),
            (
                "surfaces.interactive_shell.command_registry.settings_cmds",
                "_VERBOSE_FIRST_ARGS",
                "/verbose",
            ),
            (
                "surfaces.interactive_shell.command_registry.privacy_cmds",
                "_HISTORY_FIRST_ARGS",
                "/history",
            ),
        ],
    )
    def test_registered_completions_are_the_pickers_options(
        self, module_path: str, catalog: str, command: str
    ) -> None:
        import importlib

        module = importlib.import_module(module_path)
        options = getattr(module, catalog)
        registered = next(cmd for cmd in module.COMMANDS if cmd.name == command)

        assert registered.first_arg_completions == options
        assert all(meta.strip() for _name, meta in options), "every row needs a description"
