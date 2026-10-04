"""Session list rendering at terminal widths used by the REPL."""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime
from os import terminal_size

import pytest
from rich.console import Console

from surfaces.interactive_shell.ui.sessions import render_recent_sessions, session_menu_items


def _render(monkeypatch: pytest.MonkeyPatch, width: int) -> str:
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.rendering.shutil.get_terminal_size",
        lambda **_kwargs: terminal_size((width, 24)),
    )
    output = io.StringIO()
    console = Console(file=output, width=width, force_terminal=False, highlight=False)
    items = session_menu_items(
        [
            {
                "session_id": "6293e297aabb",
                "name": "[bold]Which demo?[/bold]",
                "started_at": "2026-10-01T15:40:00+05:30",
                "duration_secs": 999,
                "total_turns": 2,
            },
            {
                "session_id": "89b17b4daabb",
                "name": "/choose",
                "conversation_title": "Investigate API latency",
                "started_at": "2026-09-29T14:53:00+05:30",
                "duration_secs": 159,
                "total_turns": 8,
            },
        ],
        current_session_id="6293e297aabb",
        current_started_at=datetime.now(UTC).timestamp() - 5,
        resumed_from_name=None,
    )
    render_recent_sessions(console, items)
    return output.getvalue()


def test_recent_sessions_wide_table_prioritizes_names_and_keeps_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered = _render(monkeypatch, 100)

    assert "Session" in rendered and "Started" in rendered and "Turns" in rendered
    assert "● current" in rendered
    assert "[bold]Which demo?[/bold]" in rendered
    assert "6293e297" in rendered and "89b17b4d" in rendered
    assert "Investigate API latency" in rendered
    assert "999" not in rendered  # The current session shows live elapsed time.
    assert "/resume <session ID>" in rendered
    assert all(len(line) < 100 for line in rendered.splitlines())


def test_recent_sessions_compact_layout_preserves_resume_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered = _render(monkeypatch, 60)

    assert "Started" not in rendered
    assert re.search(r"6293e297  \d\d-\d\d \d\d:\d\d", rendered)
    assert re.search(r"89b17b4d  \d\d-\d\d \d\d:\d\d  2m 39s  8t", rendered)
    assert "/resume <id> to continue" in rendered
    assert all(len(line) < 60 for line in rendered.splitlines())
