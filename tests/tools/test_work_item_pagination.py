"""Work-item pages remain reachable through the registered agent tool."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn, cast

import pytest

from config.constants.work_items import OPENSRE_WORK_ITEMS_DIR_ENV
from core.domain.types.tools import ToolSurface
from core.domain.work_items import add_work_item, update_work_item
from tools.registry import get_tool_descriptors, load_tool
from tools.system.work_items import work_task_list


@pytest.fixture
def work_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path))
    return tmp_path


def test_registered_tool_can_retrieve_every_item_beyond_the_limit(work_dir: Path) -> None:
    ids = [add_work_item(title=f"Task {index}").id for index in range(105)]
    descriptor = next(
        item for item in get_tool_descriptors(ToolSurface.ACTION) if item.name == "work_task_list"
    )
    registered = load_tool(descriptor)
    assert registered is not None
    offset_schema = registered.input_schema["properties"]["offset"]
    assert offset_schema["type"] == "integer"
    assert offset_schema["minimum"] == offset_schema["default"] == 0
    first = registered.run()
    assert [task["id"] for task in first["tasks"]] == ids[:20]
    assert (first["offset"], first["limit"], first["next_offset"]) == (0, 20, 20)

    page = registered.run(limit=500)
    assert (page["returned"], page["total"], page["limit"]) == (100, 105, 100)
    assert page["has_more"] is True
    tail = registered.run(limit=500, offset=page["next_offset"])
    assert [task["id"] for task in page["tasks"] + tail["tasks"]] == ids
    assert (tail["returned"], tail["total"], tail["offset"]) == (5, 105, 100)
    assert tail["has_more"] is False
    assert tail["next_offset"] is None
    assert tail["path"] == str(work_dir / "work_items.json")


def test_filters_are_applied_before_the_offset(work_dir: Path) -> None:
    add_work_item(title="Other project", project="other", owner="alice")
    first = add_work_item(title="First match", project="infra", owner="alice")
    add_work_item(title="Other owner", project="infra", owner="bob")
    second = add_work_item(title="Second match", project="infra", owner="alice")
    completed = add_work_item(title="Completed", project="infra", owner="alice")
    update_work_item(first.id, changes={"status": "blocked"})
    update_work_item(completed.id, changes={"status": "completed"})

    page = work_task_list(status="active", project="INFRA", owner="ALICE", limit=1)
    tail = work_task_list(
        status="active", project="INFRA", owner="ALICE", limit=1, offset=page["next_offset"]
    )
    assert [task["id"] for task in page["tasks"] + tail["tasks"]] == [first.id, second.id]
    assert page["total"] == tail["total"] == 2
    assert (page["has_more"], tail["has_more"]) == (True, False)


@pytest.mark.parametrize(
    ("offset", "limit", "returned", "has_more", "next_offset"),
    [
        (0, 1, 1, True, 1),
        (1, 1, 1, False, None),
        (2, 1, 0, False, None),
        (9, 1, 0, False, None),
        (0, 0, 0, True, None),
        (1, 0, 0, True, None),
    ],
)
def test_page_boundaries(
    work_dir: Path,
    offset: int,
    limit: int,
    returned: int,
    has_more: bool,
    next_offset: int | None,
) -> None:
    add_work_item(title="First")
    add_work_item(title="Second")
    result = work_task_list(offset=offset, limit=limit)
    assert result["total"] == 2
    assert result["returned"] == len(result["tasks"]) == returned
    assert result["has_more"] is has_more
    assert result["next_offset"] == next_offset


def test_empty_store_has_no_continuation(work_dir: Path) -> None:
    result = work_task_list(offset=4)
    assert result["tasks"] == []
    assert (result["returned"], result["total"], result["offset"]) == (0, 0, 4)
    assert result["has_more"] is False
    assert result["next_offset"] is None


@pytest.mark.parametrize("offset", [-1, True, "2", 1.5, None])
def test_invalid_offset_is_rejected_before_reading_store(
    work_dir: Path, offset: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_read(**_kwargs: object) -> NoReturn:
        raise AssertionError("Invalid offsets must be rejected before reading the store")

    monkeypatch.setattr("tools.system.work_items.tool.list_work_items", unexpected_read)
    result = work_task_list(offset=cast(int, offset))
    assert result == {"error": "invalid_offset", "detail": "offset must be a non-negative integer"}
    assert not (work_dir / "work_items.json").exists()
