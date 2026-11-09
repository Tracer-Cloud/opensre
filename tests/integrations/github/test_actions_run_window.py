"""Run listings can be narrowed to a window, and say what the narrowing cost.

Asked "what's the current error rate on github?", the agent had no tool that
understood a time window, so it built its own query and chose the window each
turn. One run it picked the last hour, found no completed runs, and answered
"N/A" with three goal turns unused.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from integrations.github.tools.actions import (
    NO_RUN_WINDOW,
    RATE_WINDOW_HOURS,
    window_runs,
)

_NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
_PAGE = 30


def _run(hours_ago: float, name: str = "ci") -> dict[str, object]:
    started = _NOW - timedelta(hours=hours_ago)
    return {"name": name, "created_at": started.isoformat().replace("+00:00", "Z")}


def test_the_rate_window_is_a_day_not_an_hour() -> None:
    assert RATE_WINDOW_HOURS == 24


def test_the_tool_default_is_no_window_so_forensics_still_sees_old_runs() -> None:
    """Defaulting to a window would hide the run before an incident.

    This listing also answers "which deploy failed right before the incident".
    A 24-hour default dropped a three-month-old fixture run in
    ``tests/tools/test_github_actions_tool.py`` — the same way it would drop the
    run an investigation was looking for.
    """
    assert NO_RUN_WINDOW == 0


def test_runs_older_than_the_window_are_dropped() -> None:
    # Arrange
    runs = [_run(1, "recent"), _run(5, "today"), _run(30, "yesterday")]

    # Act
    windowed = window_runs(runs, window_hours=24, now=_NOW, page_limit=_PAGE)

    # Assert
    assert [run["name"] for run in windowed.runs] == ["recent", "today"]


def test_a_page_reaching_past_the_window_is_fully_fetched() -> None:
    # Arrange: an older run came back, so the page spans the whole window.
    # Act
    windowed = window_runs(
        [_run(1), _run(30)],
        window_hours=24,
        now=_NOW,
        page_limit=_PAGE,
    )

    # Assert
    assert windowed.window_fully_fetched is True


def test_a_full_page_that_ends_inside_the_window_is_not_fully_fetched() -> None:
    # Arrange: every run fetched is recent and the page is full — more may exist.
    runs = [_run(1), _run(2), _run(3)]

    # Act
    windowed = window_runs(runs, window_hours=24, now=_NOW, page_limit=len(runs))

    # Assert: a count over this is a floor, not a total.
    assert len(windowed.runs) == 3
    assert windowed.window_fully_fetched is False


def test_an_exhausted_page_all_inside_the_window_is_fully_fetched() -> None:
    # Arrange: fewer runs than per_page — the listing has nothing left to miss.
    runs = [_run(1), _run(2), _run(3)]

    # Act
    windowed = window_runs(runs, window_hours=24, now=_NOW, page_limit=_PAGE)

    # Assert: every in-window run that exists is on this page.
    assert len(windowed.runs) == 3
    assert windowed.window_fully_fetched is True


def test_an_empty_page_is_fully_fetched() -> None:
    windowed = window_runs([], window_hours=24, now=_NOW, page_limit=_PAGE)

    assert windowed.runs == []
    assert windowed.window_fully_fetched is True


def test_undated_runs_are_counted_rather_than_vanishing() -> None:
    # Arrange: a malformed created_at cannot be placed in or out of the window.
    runs = [
        _run(1, "good"),
        {"name": "broken", "created_at": "not-a-date"},
        {"name": "missing"},
    ]

    # Act
    windowed = window_runs(runs, window_hours=24, now=_NOW, page_limit=_PAGE)

    # Assert: dropped from the count, but the caller can see how many.
    assert [run["name"] for run in windowed.runs] == ["good"]
    assert windowed.undated == 2


def test_undated_runs_alone_do_not_prove_an_older_cutoff() -> None:
    # Arrange: undated rows are not older-run evidence; a full page of them is
    # still ambiguous about dated coverage. An exhausted page is complete.
    full = window_runs(
        [{"name": "broken", "created_at": ""}],
        window_hours=24,
        now=_NOW,
        page_limit=1,
    )
    exhausted = window_runs(
        [{"name": "broken", "created_at": ""}],
        window_hours=24,
        now=_NOW,
        page_limit=_PAGE,
    )

    # Assert
    assert full.window_fully_fetched is False
    assert full.undated == 1
    assert exhausted.window_fully_fetched is True
    assert exhausted.undated == 1
