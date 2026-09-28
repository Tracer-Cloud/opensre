"""Tests for the scrollable conversation picker."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from io import StringIO
from os import terminal_size
from types import SimpleNamespace
from typing import Any

from rich.console import Console

from surfaces.interactive_shell.ui import resume_picker


def test_draw_starts_with_blank_line_and_counts_it(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(resume_picker, "menu_columns", lambda: 80)

    height = resume_picker._draw(
        [
            resume_picker.ResumeMenuItem(
                session_id="session-a",
                title="Investigate latency",
                activity_at=datetime.now(UTC),
            )
        ],
        selected=0,
        top=0,
        visible_rows=1,
        erase_lines=0,
        now=datetime.now(UTC),
    )

    assert capsys.readouterr().out.startswith("\r\n")
    assert height == 7


def test_picker_recalculates_viewport_after_terminal_resize(monkeypatch: Any) -> None:
    sizes = iter((terminal_size((80, 24)), terminal_size((80, 10))))
    actions = iter(("ignore", "cancel"))
    visible_rows: list[int] = []

    monkeypatch.setattr(
        resume_picker,
        "shutil",
        SimpleNamespace(get_terminal_size=lambda **_kwargs: next(sizes)),
    )
    monkeypatch.setattr(resume_picker, "enter_inline_menu", lambda: None)
    monkeypatch.setattr(resume_picker, "leave_inline_menu", lambda: None)
    monkeypatch.setattr(resume_picker, "erase_menu_lines", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(resume_picker, "read_menu_action", lambda: next(actions))
    monkeypatch.setattr(resume_picker, "repl_tty_interactive", lambda: True)

    def _draw(*_args: Any, **kwargs: Any) -> int:
        visible_rows.append(kwargs["visible_rows"])
        return kwargs["visible_rows"] + resume_picker._CHROME_ROWS

    monkeypatch.setattr(resume_picker, "_draw", _draw)

    picked = resume_picker.choose_resume_session(
        [
            resume_picker.ResumeMenuItem(
                session_id="session-a",
                title="Investigate latency",
                activity_at=datetime.now(UTC),
            )
        ]
    )

    assert picked is None
    assert visible_rows == [17, 3]


def test_picker_does_not_enter_raw_mode_without_tty(monkeypatch: Any) -> None:
    entered_raw_mode: list[bool] = []
    monkeypatch.setattr(resume_picker, "repl_tty_interactive", lambda: False)
    monkeypatch.setattr(
        resume_picker,
        "enter_inline_menu",
        lambda: entered_raw_mode.append(True),
    )

    picked = resume_picker.choose_resume_session(
        [
            resume_picker.ResumeMenuItem(
                session_id="session-a",
                title="Investigate latency",
                activity_at=datetime.now(UTC),
            )
        ]
    )

    assert picked is None
    assert entered_raw_mode == []


def test_interactive_resume_separates_picker_from_result(monkeypatch: Any) -> None:
    resume_command = importlib.import_module(
        "surfaces.interactive_shell.command_registry.session_cmds.resume"
    )
    events: list[str] = []
    resumed: dict[str, Any] = {}
    loads: list[tuple[int, bool]] = []
    offered_sessions: list[str] = []

    def _load_recent(limit: int, *, require_conversation: bool) -> list[dict[str, str]]:
        loads.append((limit, require_conversation))
        return [
            {
                "session_id": "current-session",
                "conversation_title": "Current work",
                "activity_at": "2026-09-25T12:01:00+00:00",
            },
            {
                "session_id": "target-session",
                "conversation_title": "Investigate latency",
                "activity_at": "2026-09-25T12:00:00+00:00",
            },
        ]

    repo = SimpleNamespace(load_recent=_load_recent)

    def _prepare_output() -> None:
        events.append("gap")

    def _choose(items: list[resume_picker.ResumeMenuItem]) -> str:
        offered_sessions.extend(item.session_id for item in items)
        return "target-session"

    def _do_resume(
        prefix: str,
        session: Any,
        console: Console,
        *,
        slash_command: str | None = None,
    ) -> bool:
        events.append("resume")
        resumed.update(prefix=prefix, slash_command=slash_command)
        return True

    monkeypatch.setattr(resume_command, "default_session_repo", lambda: repo)
    monkeypatch.setattr(
        resume_command,
        "choose_resume_session",
        _choose,
    )
    monkeypatch.setattr(resume_command, "prepare_repl_output_line", _prepare_output)
    monkeypatch.setattr(resume_command, "_do_resume", _do_resume)

    handled = resume_command._interactive_resume_menu(
        SimpleNamespace(session_id="current-session"),
        Console(file=StringIO()),
    )

    assert handled is True
    assert loads == [(resume_command._RECENT_CONVERSATION_LIMIT + 1, True)]
    assert offered_sessions == ["target-session"]
    assert events == ["gap", "resume"]
    assert resumed == {
        "prefix": "target-session",
        "slash_command": "/resume target-s",
    }
