"""Viewport, focus, and untrusted-label contracts for framed menus."""

from __future__ import annotations

import re

import pytest

from surfaces.shared.terminal.components.menu_panel import build_menu_panel
from surfaces.shared.terminal.prompt_layout import prompt_text_width


@pytest.mark.parametrize(("width", "height"), [(80, 20), (39, 12), (20, 6), (8, 3), (40, 3)])
def test_panel_keeps_focused_choice_visible_and_within_terminal_cells(
    width: int, height: int
) -> None:
    labels = [f"Model {i} 界界" for i in range(20)]
    lines = build_menu_panel(
        title="Choose model",
        breadcrumb="/model › Provider",
        labels=labels,
        index=17,
        width=width,
        max_height=height,
        note="Current provider",
    )
    plain = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in lines]
    assert len(lines) <= height
    assert all(prompt_text_width(line) == width for line in plain)
    assert any("›" in line for line in plain)
    if width >= 20:
        assert any("Model 17" in line for line in plain)
        assert not any("Model 0 " in line for line in plain)


def test_panel_distinguishes_current_value_from_focus_and_strips_controls() -> None:
    lines = build_menu_panel(
        title="Theme",
        breadcrumb="/theme",
        labels=["Original", "\x1b[2JNew\nTheme"],
        index=1,
        current_index=0,
        width=80,
        max_height=20,
    )
    plain = "\n".join(re.sub(r"\x1b\[[0-9;]*m", "", line) for line in lines)
    assert "1 Original" in plain and "✓" in plain.split("1 Original")[1].split("\n")[0]
    assert "› 2 [2JNewTheme" in plain
    assert "\x1b[2J" not in "".join(lines)
    assert "/theme" not in plain
    assert "Esc close" in plain


@pytest.mark.parametrize("action,expected", [("enter", 12), ("cancel", None), ("2", 1)])
def test_panel_picker_returns_original_index_and_erases_actual_height(
    monkeypatch: pytest.MonkeyPatch, action: str, expected: int | None
) -> None:
    import io
    import sys

    from surfaces.shared.terminal.components import choice_menu

    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(choice_menu, "_cols", lambda: 40)
    monkeypatch.setattr(choice_menu, "_viewport_rows", lambda: 10)
    monkeypatch.setattr(choice_menu, "_read_action", lambda: action)
    result = choice_menu._pick(
        title="Models",
        crumb="/model › Provider",
        labels=[f"Model {i}" for i in range(20)],
        initial_index=12,
        current_index=2,
        panel=True,
    )
    assert result == expected
    rendered = output.getvalue()
    assert "13 Model 12" in rendered
    assert "\x1b[9A" in rendered and "\x1b[9M" in rendered
    assert rendered.count("\n") == 9


def test_panel_erases_reflowed_rows_when_dismissed_after_width_shrink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io
    import sys

    from surfaces.shared.terminal.components import choice_menu

    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(choice_menu, "_cols", lambda: 40)
    monkeypatch.setattr(choice_menu, "_viewport_rows", lambda: 40)

    def shrink_and_cancel() -> str:
        monkeypatch.setattr(choice_menu, "_cols", lambda: 20)
        return "cancel"

    monkeypatch.setattr(choice_menu, "_read_action", shrink_and_cancel)
    assert (
        choice_menu._pick(title="Theme", crumb="/theme", labels=["one", "two"], panel=True) is None
    )
    assert "\x1b[12A" in output.getvalue()
    assert "\x1b[12M" in output.getvalue()
