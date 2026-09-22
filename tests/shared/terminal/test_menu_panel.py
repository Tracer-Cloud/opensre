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
    panel = build_menu_panel(
        title="Choose model",
        breadcrumb="/model › Provider",
        labels=labels,
        index=17,
        width=width,
        max_height=height,
        note="Current provider",
    )
    lines = panel.lines
    plain = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in lines]
    assert len(lines) <= height
    assert all(prompt_text_width(line) == width for line in plain)
    assert any("›" in line for line in plain)
    if width >= 20:
        assert any("Model 17" in line for line in plain)
        assert not any("Model 0 " in line for line in plain)


def test_panel_distinguishes_current_value_from_focus_and_strips_controls() -> None:
    panel = build_menu_panel(
        title="Theme",
        breadcrumb="/theme",
        labels=["Original", "\x1b[2JNew\nTheme"],
        index=1,
        current_index=0,
        width=80,
        max_height=20,
    )
    lines = panel.lines
    plain = "\n".join(re.sub(r"\x1b\[[0-9;]*m", "", line) for line in lines)
    assert "1 Original" in plain and "✓" in plain.split("1 Original")[1].split("\n")[0]
    assert "› 2 [2JNewTheme" in plain
    assert "\x1b[2J" not in "".join(lines)
    assert "/theme" not in plain
    assert "Esc close" in plain


@pytest.mark.parametrize("action,expected", [("enter", 12), ("cancel", None), ("2", 10)])
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
    assert "4 Model 12" in rendered
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


@pytest.mark.parametrize("shortcut", ["1", "2"])
def test_tiny_panel_shortcuts_cannot_select_hidden_choices(
    monkeypatch: pytest.MonkeyPatch, shortcut: str
) -> None:
    from surfaces.shared.terminal.components import choice_menu

    actions = iter([shortcut, "enter"])
    monkeypatch.setattr(choice_menu, "_cols", lambda: 40)
    monkeypatch.setattr(choice_menu, "_viewport_rows", lambda: 4)
    monkeypatch.setattr(choice_menu, "_read_action", lambda: next(actions))
    assert (
        choice_menu._pick(
            title="Models",
            crumb="/model",
            labels=[f"Model {i}" for i in range(20)],
            initial_index=12,
            panel=True,
        )
        == 12
    )


@pytest.mark.parametrize(("width", "height"), [(80, 20), (39, 10), (15, 6), (8, 3)])
def test_details_wrap_and_scroll_without_losing_model_text(width: int, height: int) -> None:
    from surfaces.shared.terminal.components.detail_panel import build_detail_panel

    model = "custom-model-" + "x" * 80
    seen: list[str] = []
    offset = 0
    while True:
        lines, actual, limit = build_detail_panel(
            "Configuration",
            [("Reasoning model", model)],
            width=width,
            height=height,
            offset=offset,
        )
        plain = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in lines]
        assert len(lines) <= height
        assert all(prompt_text_width(line) == min(width, 60) for line in plain)
        if offset == 0:
            seen.extend(plain[1:-3] if width >= 12 and height >= 5 else plain)
        elif actual == offset:
            seen.append(plain[-4] if width >= 12 and height >= 5 else plain[-1])
        if offset >= limit:
            break
        offset += 1
    assert model in "".join(line.strip(" │") for line in seen)


@pytest.mark.parametrize("dismiss", ["enter", "cancel"])
@pytest.mark.parametrize(("paint_width", "columns", "reflow"), [(39, 20, 2), (120, 60, 1)])
def test_details_dismiss_and_erase_on_enter_or_escape(
    monkeypatch: pytest.MonkeyPatch, dismiss: str, paint_width: int, columns: int, reflow: int
) -> None:
    import io
    import sys

    from surfaces.shared.terminal.components import choice_menu, detail_panel

    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(choice_menu, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_menu, "_clear_prompt_toolkit_paint", lambda: None)
    monkeypatch.setattr(detail_panel, "drain_stale_cpr_bytes", lambda: None)
    monkeypatch.setattr(choice_menu, "_menu_paint_width", lambda: paint_width)
    monkeypatch.setattr(choice_menu, "_viewport_rows", lambda: 20)
    monkeypatch.setattr(choice_menu, "_cols", lambda: columns)
    monkeypatch.setattr(choice_menu, "_read_action", lambda: dismiss)
    erased: list[int] = []
    left: list[bool] = []
    monkeypatch.setattr(choice_menu, "_erase_menu_block", lambda n, **_: erased.append(n))
    monkeypatch.setattr(choice_menu, "leave_inline_menu", lambda: left.append(True))
    detail_panel.repl_show_details(title="Configuration", fields=[("Provider", "OpenAI")])
    assert erased == [output.getvalue().count("\n") * reflow]
    assert left == [True]


def test_details_use_compact_columns_and_secondary_metadata() -> None:
    import infrastructure.terminal.theme as theme
    from surfaces.shared.terminal.components.detail_panel import build_detail_panel

    rows, offset, limit = build_detail_panel(
        "Model › Configuration",
        [("Provider", "OpenAI"), ("Reasoning", "gpt-5.6-sol"), ("Tool calls", "gpt-5.6-sol")],
        note="Managed by OpenSRE account",
        width=160,
        height=40,
    )
    assert len(rows) == 9
    assert offset == limit == 0
    assert all(prompt_text_width(re.sub(r"\x1b\[[0-9;]*m", "", row)) == 60 for row in rows)
    assert f"{theme.DIM_ANSI}Provider" in rows[1]
    assert f"{theme.TEXT_ANSI}OpenAI" in rows[1]
