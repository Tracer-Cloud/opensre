"""Tests for terminal-only progress during ``opensre ask``."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from infrastructure.terminal.spinner_frames import DOT_SPINNER_FRAMES
from infrastructure.terminal.theme import BRAND, HIGHLIGHT
from surfaces.cli.ask import progress
from surfaces.cli.ask.progress import status_for_tool_event


def test_tool_progress_names_the_active_tool_without_its_input() -> None:
    status = status_for_tool_event(
        "tool_start",
        {"name": "grafana_query", "input": {"query": "secret label value"}},
    )

    assert status == "Invoking tools… · grafana query"
    assert "secret" not in status


def test_tool_progress_ignores_non_tool_or_unnamed_events() -> None:
    assert status_for_tool_event("tool_end", {"name": "grafana_query"}) == "Thinking…"
    assert status_for_tool_event("message_update", {}) is None
    assert status_for_tool_event("tool_start", {}) is None


def test_tool_progress_uses_a_neutral_label_for_batched_tool_starts() -> None:
    first_start = {"name": "grafana_query", "tool_call_count": 2, "tool_call_index": 0}
    second_start = {"name": "logs_search", "tool_call_count": 2, "tool_call_index": 1}

    assert status_for_tool_event("tool_start", first_start) == "Invoking 2 tools…"
    assert status_for_tool_event("tool_start", second_start) is None


def test_progress_scope_starts_before_the_agent_turn_and_stops_afterward(monkeypatch) -> None:
    events: list[str] = []

    class _Progress:
        def __enter__(self) -> _Progress:
            events.append("start")
            return self

        def __exit__(self, *_args: object) -> None:
            events.append("stop")

        def __call__(self, _kind: str, _data: dict[str, Any]) -> None:
            """Observe tool lifecycle events."""

    monkeypatch.setattr(progress, "AskProgress", _Progress)

    with progress.ask_progress_scope(enabled=True) as observer:
        assert observer is not None
        assert events == ["start"]

    assert events == ["start", "stop"]


def test_progress_leaves_a_blank_line_before_the_activity_row(monkeypatch) -> None:
    events: list[str] = []

    class _Console:
        def print(self) -> None:
            events.append("spacing")

    class _Progress:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            """Record construction without rendering a terminal."""

        def add_task(self, _description: str, **_kwargs: object) -> object:
            return "ask"

        def start(self) -> None:
            events.append("start")

    monkeypatch.setattr(progress, "Console", lambda **_kwargs: _Console())
    monkeypatch.setattr(progress, "Progress", _Progress)

    progress.AskProgress().__enter__()

    assert events == ["spacing", "start"]


def test_progress_updates_the_visible_status_when_a_tool_starts(monkeypatch) -> None:
    updates: list[tuple[object, str]] = []

    class _Progress:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            """Record progress construction without rendering a terminal."""

        def add_task(self, _description: str, *, total: object, tool_active: bool) -> object:
            assert total is None
            assert tool_active is False
            return "ask"

        def start(self) -> None:
            """Start progress rendering."""

        def stop(self) -> None:
            """Stop progress rendering."""

        def update(self, task_id: object, *, description: str, tool_active: bool) -> None:
            updates.append((task_id, description, tool_active))

    monkeypatch.setattr(progress, "Progress", _Progress)
    reporter = progress.AskProgress()
    reporter("tool_start", {"name": "grafana_query"})
    reporter("tool_end", {"name": "grafana_query"})

    assert updates == [
        ("ask", "Invoking tools… · grafana query", True),
        ("ask", "Thinking…", False),
    ]


def test_progress_uses_shared_terminal_spinner_frames_and_activity_colors(monkeypatch) -> None:
    monkeypatch.setattr(progress, "spinner_frames", lambda: DOT_SPINNER_FRAMES)
    spinner = progress._ActivitySpinnerColumn()

    thinking = spinner.render(SimpleNamespace(elapsed=0.0, fields={}))
    running_tool = spinner.render(SimpleNamespace(elapsed=0.0, fields={"tool_active": True}))

    assert thinking.plain == DOT_SPINNER_FRAMES[0]
    assert str(thinking.style) == str(HIGHLIGHT)
    assert str(running_tool.style) == str(BRAND)
