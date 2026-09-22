"""Search keeps stable choice indexes and cannot select an empty result row."""

from __future__ import annotations

import sys
from io import StringIO
from typing import Any

import pytest

from surfaces.shared.terminal.components import choice_menu, key_reader
from surfaces.shared.terminal.components.menu_search import matching_indices, searchable_text


def test_search_matches_words_in_labels_and_metadata_without_patterns() -> None:
    text = searchable_text(["Purple", "Ocean [blue]", "Other"], ["current", "theme-42", ""])
    assert matching_indices(text, "BLUE 42") == [1]
    assert matching_indices(text, "[blue]") == [1]
    assert matching_indices(text, ".*") == []


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (["/", "b", "l", "u", "e", "enter"], 2),
        (["/", "x", "enter", "backspace", "b", "enter"], 2),
        (["/", "x", "cancel", "2"], 1),
        (["/", "cancel", "cancel"], None),
        (["/", "x", "eof"], None),
        (["/", "4", "2", "enter"], 2),
        (["/", "👩", "\u200d", "💻", "enter"], 2),
    ],
)
def test_filter_selection_maps_to_original_index_and_empty_state_is_not_selectable(
    monkeypatch: pytest.MonkeyPatch,
    keys: list[str],
    expected: int | None,
) -> None:
    actions = iter(keys)
    output = StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(choice_menu, "_cols", lambda: 60)
    monkeypatch.setattr(choice_menu, "_viewport_rows", lambda: 12)
    monkeypatch.setattr(key_reader, "read_menu_or_char", lambda **_: next(actions))
    answered: list[tuple[int, ...]] = []

    def answer(indices: tuple[int, ...], _text: str | None) -> None:
        answered.append(indices)

    result = choice_menu._pick(
        title="Theme",
        crumb="/theme",
        labels=["Purple", "Orange", "Blue 👩‍💻"],
        choice_notes=["", "", "theme-42"],
        panel=True,
        searchable=True,
        current_index=0,
        on_answer=answer,
    )
    assert result == expected
    assert answered == ([] if expected is None else [(expected,)])
    assert "Search:" in output.getvalue()
    assert "Esc clear" in output.getvalue()


def test_filter_current_marker_is_independent_from_matching_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = iter(["/", "b", "down", "enter"])
    monkeypatch.setattr(key_reader, "read_menu_or_char", lambda **_: next(actions))
    captured: list[dict[str, Any]] = []
    build = choice_menu.build_menu_panel

    def render(**kwargs: Any) -> Any:
        captured.append(kwargs)
        return build(**kwargs)

    monkeypatch.setattr(choice_menu, "build_menu_panel", render)
    assert (
        choice_menu._pick(
            title="Models",
            crumb="/model",
            labels=["red", "blue", "black"],
            panel=True,
            searchable=True,
            current_index=1,
        )
        == 2
    )
    last = captured[-1]
    assert last["labels"] == ["blue", "black"]
    assert last["index"] == 1
    assert last["current_index"] == 0
    assert last["numbered"] is False


@pytest.mark.skipif(sys.platform == "win32", reason="Unix PTY input contract")
@pytest.mark.timeout(5)
def test_search_reads_unicode_and_restores_terminal_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import pty
    import termios
    from types import SimpleNamespace

    master, slave = pty.openpty()
    try:
        before = termios.tcgetattr(slave)
        monkeypatch.setattr(sys, "stdin", SimpleNamespace(fileno=lambda: slave))
        os.write(master, "界👩‍💻sun".encode())
        assert key_reader.read_menu_or_char(allow_chars=True, unicode_input=True) == "界"
        assert termios.tcgetattr(slave) == before
        assert (
            "".join(
                key_reader.read_menu_or_char(allow_chars=True, unicode_input=True) for _ in range(6)
            )
            == "👩‍💻sun"
        )
        assert termios.tcgetattr(slave) == before
    finally:
        os.close(master)
        os.close(slave)


def test_search_windows_unicode_and_arrow_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    chars = iter(["界", "\ud83d", "\udc69", "\u200d", "\ud83d", "\udcbb", "\xe0", "P"])
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(getwch=lambda: next(chars)))
    assert key_reader._read_menu_or_char_windows(allow_chars=True, unicode_input=True) == "界"
    assert (
        "".join(
            key_reader._read_menu_or_char_windows(allow_chars=True, unicode_input=True)
            for _ in range(3)
        )
        == "👩‍💻"
    )
    assert key_reader._read_menu_or_char_windows(allow_chars=True, unicode_input=True) == "down"


@pytest.mark.parametrize(("width", "height"), [(80, 20), (39, 10), (20, 5), (8, 3)])
def test_search_empty_state_and_field_fit_small_terminals(width: int, height: int) -> None:
    import re

    from surfaces.shared.terminal.components.menu_panel import build_menu_panel
    from surfaces.shared.terminal.prompt_layout import prompt_text_width

    panel = build_menu_panel(
        title="Theme",
        breadcrumb="/theme",
        labels=[],
        index=0,
        width=width,
        max_height=height,
        searchable=True,
        search_query="missing",
        numbered=False,
    )
    assert not panel.choice_indices
    assert 0 < len(panel.lines) <= height
    assert all(
        prompt_text_width(re.sub(r"\x1b\[[0-9;]*m", "", line)) == width for line in panel.lines
    )


@pytest.mark.parametrize("height", [5, 6, 10])
def test_search_keeps_query_tail_and_caret_visible(height: int) -> None:
    from surfaces.shared.terminal.components.menu_panel import build_menu_panel

    panel = build_menu_panel(
        title="Theme",
        breadcrumb="/theme",
        labels=["Blue"],
        index=0,
        width=30,
        max_height=height,
        searchable=True,
        search_query="a very long query ending界",
        numbered=False,
    )
    assert "界▏" in "".join(panel.lines)


def test_short_search_panel_keeps_query_visible_when_many_matches_scroll() -> None:
    from surfaces.shared.terminal.components.menu_panel import build_menu_panel

    panel = build_menu_panel(
        title="Models",
        breadcrumb="/model",
        labels=["Blue"] * 20,
        index=12,
        width=20,
        max_height=5,
        searchable=True,
        search_query="Blue",
        numbered=False,
    )
    assert "/Blue▏" in "".join(panel.lines)
    assert list(panel.choice_indices) == [12]
