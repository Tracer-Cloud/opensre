"""Tests for terminal-only progress during ``opensre ask``."""

from __future__ import annotations

from typing import Any

from surfaces.cli.ask import progress
from surfaces.cli.ask.progress import status_for_tool_event


def test_tool_progress_names_the_active_tool_without_its_input() -> None:
    status = status_for_tool_event(
        "tool_start",
        {"name": "grafana_query", "input": {"query": "secret label value"}},
    )

    assert status == "Running grafana query…"
    assert "secret" not in status


def test_tool_progress_ignores_non_tool_or_unnamed_events() -> None:
    assert status_for_tool_event("tool_end", {"name": "grafana_query"}) is None
    assert status_for_tool_event("tool_start", {}) is None


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


def test_progress_updates_the_visible_status_when_a_tool_starts(monkeypatch) -> None:
    updates: list[tuple[object, str]] = []

    class _Progress:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            """Record progress construction without rendering a terminal."""

        def add_task(self, _description: str, *, total: object) -> object:
            assert total is None
            return "ask"

        def start(self) -> None:
            """Start progress rendering."""

        def stop(self) -> None:
            """Stop progress rendering."""

        def update(self, task_id: object, *, description: str) -> None:
            updates.append((task_id, description))

    monkeypatch.setattr(progress, "Progress", _Progress)
    reporter = progress.AskProgress()
    reporter("tool_start", {"name": "grafana_query"})

    assert updates == [("ask", "Running grafana query…")]
