"""Keyboard navigation and raw-terminal safety for the session picker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from os import terminal_size
from typing import Any

import pytest
from rich.text import Text

from surfaces.interactive_shell.ui import resume_picker, scrollable_picker, session_picker
from surfaces.shared.terminal.prompt_layout import prompt_text_width


def _items() -> list[session_picker.SessionMenuItem]:
    return [
        session_picker.SessionMenuItem("current", "Current work", "10-01 16:00", "7s", "2", True),
        session_picker.SessionMenuItem("past-a", "Investigate latency", "10-01 15:40", "11m", "4"),
        session_picker.SessionMenuItem("past-b", "Review alerts", "09-29 14:53", "2m", "8"),
    ]


def test_arrows_select_previous_session_and_restore_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_rows: list[int] = []
    events: list[str] = []
    actions = iter(("down", "enter"))

    monkeypatch.setattr(scrollable_picker, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(
        scrollable_picker.shutil,
        "get_terminal_size",
        lambda **_kwargs: terminal_size((80, 24)),
    )
    monkeypatch.setattr(scrollable_picker, "enter_inline_menu", lambda: events.append("enter"))
    monkeypatch.setattr(scrollable_picker, "leave_inline_menu", lambda: events.append("leave"))
    monkeypatch.setattr(
        scrollable_picker,
        "erase_menu_lines",
        lambda _height, *, delete=False: events.append("erase" if delete else "redraw"),
    )
    monkeypatch.setattr(scrollable_picker, "read_menu_action", lambda: next(actions))

    def _draw(_items: Any, **kwargs: Any) -> int:
        selected_rows.append(kwargs["selected"])
        return kwargs["visible_rows"] + resume_picker._CHROME_ROWS

    monkeypatch.setattr(resume_picker, "_draw", _draw)

    assert session_picker.choose_recent_session(_items()) == "past-b"
    assert selected_rows == [1, 2]
    assert events == ["enter", "erase", "leave"]


def test_recent_previous_highlight_follows_activity_order(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    items = [
        session_picker.SessionMenuItem("current", "Current", "", "", "", True, activity_at=now),
        session_picker.SessionMenuItem(
            "started-newer", "Older activity", "", "", "", activity_at=now - timedelta(days=2)
        ),
        session_picker.SessionMenuItem(
            "active-recent", "Recent activity", "", "", "", activity_at=now - timedelta(hours=1)
        ),
    ]

    def _choose(
        rows: list[resume_picker.ResumeMenuItem], **kwargs: Any
    ) -> resume_picker.ResumeMenuItem:
        assert [item.session_id for item in rows] == ["current", "active-recent", "started-newer"]
        assert rows[kwargs["selected"]].detail.startswith("active-r")
        return rows[kwargs["selected"]]

    monkeypatch.setattr(resume_picker, "choose_scrollable", _choose)

    assert session_picker.choose_recent_session(items) == "active-recent"


def test_picker_row_clips_untrusted_text_to_terminal_width() -> None:
    item = resume_picker.ResumeMenuItem(
        session_id="past-a",
        title="\x1b[31mVery long incident name with emoji 🔥 and more words",
        activity_at=None,
    )

    row = resume_picker._row(item, selected=False, index=0, width=40, now=datetime.now(UTC))

    assert "\x1b[31m" not in row
    assert prompt_text_width(Text.from_ansi(row).plain) == 40


def test_picker_scrolls_to_sessions_below_the_viewport(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        session_picker.SessionMenuItem(
            f"session-{index}", f"Work {index}", "10-01 16:00", "1m", "2"
        )
        for index in range(20)
    ]
    actions = iter(["down"] * 17 + ["enter"])
    viewport_tops: list[int] = []
    monkeypatch.setattr(scrollable_picker, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(
        scrollable_picker.shutil,
        "get_terminal_size",
        lambda **_kwargs: terminal_size((80, 24)),
    )
    monkeypatch.setattr(scrollable_picker, "enter_inline_menu", lambda: None)
    monkeypatch.setattr(scrollable_picker, "leave_inline_menu", lambda: None)
    monkeypatch.setattr(scrollable_picker, "erase_menu_lines", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scrollable_picker, "read_menu_action", lambda: next(actions))

    def _draw(_items: Any, **kwargs: Any) -> int:
        viewport_tops.append(kwargs["top"])
        return kwargs["visible_rows"] + resume_picker._CHROME_ROWS

    monkeypatch.setattr(resume_picker, "_draw", _draw)

    assert session_picker.choose_recent_session(items) == "session-17"
    assert viewport_tops[-1] > 0


def test_escape_closes_picker_and_restores_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(scrollable_picker, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(scrollable_picker, "enter_inline_menu", lambda: events.append("enter"))
    monkeypatch.setattr(scrollable_picker, "leave_inline_menu", lambda: events.append("leave"))
    monkeypatch.setattr(
        scrollable_picker,
        "erase_menu_lines",
        lambda _height, *, delete=False: events.append("erase" if delete else "redraw"),
    )
    monkeypatch.setattr(scrollable_picker, "read_menu_action", lambda: "cancel")
    monkeypatch.setattr(resume_picker, "_draw", lambda *_args, **_kwargs: 7)

    assert session_picker.choose_recent_session(_items()) is None
    assert events == ["enter", "erase", "leave"]


def test_sessions_uses_full_width_resume_layout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(resume_picker, "menu_columns", lambda: 120)
    now = datetime.now(UTC)
    resume_picker._draw(
        [
            resume_picker.ResumeMenuItem(
                session_id="current",
                title="Current work",
                activity_at=now,
                is_current=True,
                detail="current  ·  10-01 16:00  ·  7s  ·  2 turns",
            )
        ],
        selected=0,
        top=0,
        visible_rows=1,
        erase_lines=0,
        now=now,
        heading="Sessions  ·  1 recent",
    )

    lines = [Text.from_ansi(line).plain for line in capsys.readouterr().out.splitlines()]
    assert "Sessions  ·  1 recent" in "\n".join(lines)
    assert any("● current  Current work" in line for line in lines)
    assert any("current  ·  10-01 16:00  ·  7s  ·  2 turns" in line for line in lines)
    assert any("Enter already here" in line for line in lines)
    assert max(prompt_text_width(line) for line in lines) == 120


def test_matching_titles_keep_selected_id_visible_at_narrow_width(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(resume_picker, "menu_columns", lambda: 42)
    now = datetime.now(UTC)
    items = [
        resume_picker.ResumeMenuItem("first-id", "Same title", now, detail="first-id  ·  3 turns"),
        resume_picker.ResumeMenuItem("second-id", "Same title", now, detail="second-i  ·  4 turns"),
        resume_picker.ResumeMenuItem("third-id", "Same title", now, detail="third-id  ·  5 turns"),
        resume_picker.ResumeMenuItem("fourth-id", "Same title", now, detail="fourth-i  ·  6 turns"),
    ]
    resume_picker._draw(
        items, selected=1, top=1, visible_rows=2, erase_lines=0, now=now, heading="Sessions"
    )

    lines = [Text.from_ansi(line).plain for line in capsys.readouterr().out.splitlines()]
    assert any("↑↓" in line and "second-i" in line and "2/4" in line for line in lines)
