"""Due-date filters preserve local-time semantics and accurate filtered counts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

import pytest

from config.constants.work_items import OPENSRE_WORK_ITEMS_DIR_ENV
from core.domain.types.tools import ToolSurface
from core.domain.work_items import add_work_item, make_work_item, update_work_item
from tools.registry import get_tool_descriptors, load_tool
from tools.system.work_items import work_task_list
from tools.system.work_items._evidence import map_work_task_list
from tools.system.work_items.due_dates import filter_due_dates, parse_due_date_filter


@pytest.fixture
def work_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path))
    return tmp_path


def test_registered_tool_filters_local_day_before_limiting(work_dir: Path) -> None:
    add_work_item(title="Undated", project="infra", owner="alice", priority="urgent")
    add_work_item(
        title="Previous day", due_at="2026-10-29T18:29:59Z", project="infra", owner="alice"
    )
    lower = add_work_item(
        title="At start", due_at="2026-10-29T18:30Z", project="infra", owner="alice"
    )
    add_work_item(title="Other project", due_at="2026-10-30T12:00", project="other", owner="alice")
    add_work_item(title="Other owner", due_at="2026-10-30T12:00", project="infra", owner="bob")
    local = add_work_item(
        title="Local noon", due_at="2026-10-30T12:00", project="infra", owner="alice"
    )
    add_work_item(title="At end", due_at="2026-10-30T18:30Z", project="infra", owner="alice")
    descriptor = next(
        item for item in get_tool_descriptors(ToolSurface.ACTION) if item.name == "work_task_list"
    )
    registered = load_tool(descriptor)
    assert registered is not None
    properties = registered.input_schema["properties"]
    assert properties["due_after"]["type"] == properties["due_before"]["type"] == "string"
    assert properties["timezone"]["default"] == "UTC"
    assert properties["overdue_only"]["type"] == "boolean"
    args = {
        "project": "INFRA",
        "owner": "ALICE",
        "due_after": "2026-10-30",
        "due_before": "2026-10-31",
        "timezone": "Asia/Kolkata",
    }
    result = registered.run(**args, limit=1)
    assert [task["id"] for task in result["tasks"]] == [lower.id]
    assert (result["returned"], result["total"]) == (1, 2)
    assert result["due_filter"]["due_after"] == "2026-10-29T18:30:00+00:00"
    assert result["due_filter"]["due_before"] == "2026-10-30T18:30:00+00:00"
    all_matches = registered.run(**args)
    assert [task["id"] for task in all_matches["tasks"]] == [lower.id, local.id]
    evidence: dict[str, Any] = {}
    map_work_task_list(evidence, result, args)
    summary = evidence["catalog_entries"][0]["summary"]
    assert "due at or after 2026-10-29T18:30:00+00:00" in summary
    assert "due before 2026-10-30T18:30:00+00:00" in summary


def test_overdue_never_includes_completed_tasks(work_dir: Path) -> None:
    open_task = add_work_item(title="Old open", due_at="2000-01-01")
    blocked = add_work_item(title="Old blocked", due_at="2000-01-01")
    completed = add_work_item(title="Old completed", due_at="2000-01-01")
    update_work_item(blocked.id, changes={"status": "blocked"})
    update_work_item(completed.id, changes={"status": "completed"})
    add_work_item(title="Future", due_at="2099-01-01")
    add_work_item(title="Undated")
    result = work_task_list(status="all", overdue_only=True)
    assert [task["id"] for task in result["tasks"]] == [open_task.id, blocked.id]
    assert datetime.fromisoformat(result["due_filter"]["overdue_as_of"]).tzinfo is not None
    assert work_task_list(status="completed", overdue_only=True)["total"] == 0


def test_overdue_cutoff_and_explicit_range_are_both_enforced() -> None:
    cutoff = datetime(2026, 10, 30, 12, tzinfo=UTC)
    criteria = parse_due_date_filter(due_after="2026-10-30", overdue_only=True, now=cutoff)
    assert criteria is not None
    items = [
        make_work_item(title=title, due_at=due)
        for title, due in (
            ("Before range", "2026-10-29T23:59:59Z"),
            ("Overdue", "2026-10-30T11:59:59Z"),
            ("Exactly now", "2026-10-30T12:00Z"),
            ("Future", "2026-10-30T12:00:01Z"),
        )
    ]
    matched, invalid = filter_due_dates(items, criteria)
    assert [item.title for item in matched] == ["Overdue"]
    assert invalid == 0


@pytest.mark.parametrize(
    ("timezone", "start", "end", "expected_start", "expected_end"),
    [
        (
            "UTC",
            "2026-10-30",
            "2026-10-31",
            "2026-10-30T00:00:00+00:00",
            "2026-10-31T00:00:00+00:00",
        ),
        (
            "America/New_York",
            "2027-03-14",
            "2027-03-15",
            "2027-03-14T05:00:00+00:00",
            "2027-03-15T04:00:00+00:00",
        ),
        (
            "America/New_York",
            "2027-11-07",
            "2027-11-08",
            "2027-11-07T04:00:00+00:00",
            "2027-11-08T05:00:00+00:00",
        ),
    ],
)
def test_calendar_days_follow_timezone_transitions(
    timezone: str, start: str, end: str, expected_start: str, expected_end: str
) -> None:
    criteria = parse_due_date_filter(due_after=start, due_before=end, timezone=timezone)
    assert criteria is not None
    assert criteria.summary()["due_after"] == expected_start
    assert criteria.summary()["due_before"] == expected_end


def test_unresolvable_stored_dates_are_reported_without_changing_default_listing(
    work_dir: Path,
) -> None:
    add_work_item(title="Invalid", due_at="not-a-date")
    add_work_item(title="DST gap", due_at="2027-03-14T02:30")
    add_work_item(title="DST fold", due_at="2027-11-07T01:30")
    add_work_item(title="Overflow", due_at="9999-12-31T23:59:59-12:00")
    valid = add_work_item(title="Explicit offset", due_at="2027-11-07T01:30-04:00")
    add_work_item(title="Undated")
    plain = work_task_list(status="all")
    assert plain["total"] == 6
    assert "due_filter" not in plain
    filtered = work_task_list(due_before="2028-01-01", timezone="America/New_York")
    assert [task["id"] for task in filtered["tasks"]] == [valid.id]
    assert filtered["invalid_due_dates"] == 4


def test_due_filters_apply_before_offset_paging(work_dir: Path) -> None:
    add_work_item(title="Undated", project="infra", priority="urgent")
    add_work_item(title="Too late", due_at="2026-11-02", project="infra", priority="urgent")
    matches = [
        add_work_item(title=f"In range {index}", due_at="2026-10-30T12:00Z", project="infra")
        for index in range(5)
    ]
    args = {"project": "infra", "due_after": "2026-10-30", "due_before": "2026-10-31"}
    pages = []
    offset: int | None = 0
    while offset is not None:
        page = work_task_list(**args, limit=2, offset=offset)
        assert page["total"] == 5
        assert page["due_filter"]["due_before"] == "2026-10-31T00:00:00+00:00"
        pages.append(page)
        offset = page["next_offset"]
    assert [page["returned"] for page in pages] == [2, 2, 1]
    assert [page["has_more"] for page in pages] == [True, True, False]
    assert [task["id"] for page in pages for task in page["tasks"]] == [item.id for item in matches]


def test_invalid_offset_is_rejected_before_due_filter_or_store(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_read(**_kwargs: object) -> NoReturn:
        raise AssertionError("Invalid offsets must not read the store")

    monkeypatch.setattr("tools.system.work_items.tool.list_work_items", unexpected_read)
    result = work_task_list(offset=-1, overdue_only=True)
    assert result["error"] == "invalid_offset"


@pytest.mark.parametrize(
    "args",
    [
        {"due_before": "not-a-date"},
        {"due_before": "2027-03-14T02:30", "timezone": "America/New_York"},
        {"due_after": "2027-11-07T01:30", "timezone": "America/New_York"},
        {"due_before": "9999-12-31T23:59:59-12:00"},
        {"due_after": "2026-10-31", "due_before": "2026-10-30"},
        {"due_after": "2026-10-30", "due_before": "2026-10-30"},
        {"overdue_only": True, "timezone": "Invalid/Zone"},
        {"overdue_only": "true"},
        {"due_before": None},
    ],
)
def test_bad_criteria_are_rejected_before_reading_store(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch, args: dict[str, Any]
) -> None:
    def unexpected_read(**_kwargs: object) -> NoReturn:
        raise AssertionError("Invalid criteria must not read the store")

    monkeypatch.setattr("tools.system.work_items.tool.list_work_items", unexpected_read)
    result = work_task_list(**args)
    assert result["error"] == "invalid_due_filter"
    assert result["detail"]
