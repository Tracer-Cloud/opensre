"""Loop definition edits keep group identity and apply atomically on current records."""

from __future__ import annotations

import json
from pathlib import Path

from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_GROUP_ID_PARAM,
    LOOP_PROMPT_PARAM,
    LOOP_REPORT_PARAM,
    LOOP_TEMPLATE_PARAM,
)
from infrastructure.scheduling.scheduler.loop_edit import edit_loop
from infrastructure.scheduling.scheduler.loops import list_loop_summaries
from infrastructure.scheduling.scheduler.storage import add_task, get_task
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind


def test_group_name_edit_preserves_different_member_settings(tmp_path: Path) -> None:
    store = tmp_path / "scheduler_tasks.json"
    first = add_task(
        ScheduledTask(
            id="task-a",
            name="Grouped report",
            kind=TaskKind.MANUAL_LOOP,
            cron="*/30 * * * * *",
            provider=Provider.SLACK,
            chat_id="C123",
            last_run="2026-10-14T00:00:00Z",
            next_run="2026-10-14T00:00:30Z",
            params={
                LOOP_GROUP_ID_PARAM: "group-a",
                LOOP_PROMPT_PARAM: "Review API",
                LOOP_REPORT_PARAM: "ci_repair",
                "custom": "first",
            },
        ),
        store,
    )
    second = add_task(
        ScheduledTask(
            id="task-b",
            name="Grouped report",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * mon",
            provider=Provider.TELEGRAM,
            chat_id="T123",
            enabled=False,
            window_hours=168,
            params={
                LOOP_GROUP_ID_PARAM: "group-a",
                LOOP_PROMPT_PARAM: "Review API",
                "custom": "second",
            },
        ),
        store,
    )
    result, error = edit_loop(
        (first.id, second.id), {"name": "Renamed group"}, loop_id="group-a", store_path=store
    )
    assert result is not None and not error
    saved_first = get_task(first.id, store)
    saved_second = get_task(second.id, store)
    assert saved_first is not None and saved_second is not None
    assert saved_first.name == saved_second.name == "Renamed group"
    assert saved_first.model_dump(exclude={"name"}) == first.model_dump(exclude={"name"})
    assert saved_second.model_dump(exclude={"name"}) == second.model_dump(exclude={"name"})


def test_managed_prompt_failure_rolls_back_whole_group(tmp_path: Path) -> None:
    store = tmp_path / "scheduler_tasks.json"
    first = add_task(
        ScheduledTask(
            id="task-a",
            name="Grouped report",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 8 * * *",
            provider=Provider.SLACK,
            params={LOOP_GROUP_ID_PARAM: "group-a", LOOP_PROMPT_PARAM: "Original prompt"},
        ),
        store,
    )
    second = add_task(
        ScheduledTask(
            id="task-b",
            name="Grouped report",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 8 * * *",
            provider=Provider.TELEGRAM,
            params={
                LOOP_GROUP_ID_PARAM: "group-a",
                LOOP_TEMPLATE_PARAM: "pr-review",
                LOOP_PROMPT_PARAM: "Original prompt",
            },
        ),
        store,
    )
    before = store.read_bytes()
    result, error = edit_loop(
        (first.id, second.id),
        {"name": "Changed", "prompt": "A replacement prompt"},
        loop_id="group-a",
        store_path=store,
    )
    assert result is None and "managed instructions" in error
    assert store.read_bytes() == before


def test_missing_or_regrouped_member_does_not_save_partial_edit(tmp_path: Path) -> None:
    store = tmp_path / "scheduler_tasks.json"
    task = add_task(
        ScheduledTask(
            id="task-a",
            name="Original",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 8 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_GROUP_ID_PARAM: "group-new", LOOP_PROMPT_PARAM: "Original prompt"},
        ),
        store,
    )
    before = store.read_bytes()
    for task_ids, loop_id in (((task.id, "missing"), "group-new"), ((task.id,), "group-old")):
        result, error = edit_loop(task_ids, {"name": "Changed"}, loop_id=loop_id, store_path=store)
        assert result is None and error
        assert store.read_bytes() == before


def test_added_group_member_rejects_stale_edit_before_save(tmp_path: Path) -> None:
    store = tmp_path / "scheduler_tasks.json"
    first = add_task(
        ScheduledTask(
            id="task-a",
            name="Original",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 8 * * *",
            provider=Provider.SLACK,
            params={LOOP_GROUP_ID_PARAM: "group-a", LOOP_PROMPT_PARAM: "Original prompt"},
        ),
        store,
    )
    add_task(
        ScheduledTask(
            id="task-b",
            name="Original",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 8 * * *",
            provider=Provider.TELEGRAM,
            params={LOOP_GROUP_ID_PARAM: "group-a", LOOP_PROMPT_PARAM: "Original prompt"},
        ),
        store,
    )
    before = store.read_bytes()
    result, error = edit_loop((first.id,), {"name": "Changed"}, loop_id="group-a", store_path=store)
    assert result is None and "group changed" in error
    assert store.read_bytes() == before


def test_org_bound_edit_rechecks_current_owner_before_patch(tmp_path: Path) -> None:
    store = tmp_path / "scheduler_tasks.json"
    task = add_task(
        ScheduledTask(
            id="task-a",
            name="Original",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 8 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            organization="org-b",
            params={LOOP_PROMPT_PARAM: "Original prompt"},
        ),
        store,
    )
    before = store.read_bytes()
    scope = StorageScope(principal=Principal.org("org-a"), actor=Actor(id="actor-a"))
    with bound_storage_scope(scope):
        result, error = edit_loop(
            (task.id,), {"name": "Changed"}, loop_id=task.id, store_path=store
        )
    assert result is None and "not available in this organization" in error
    assert store.read_bytes() == before


def test_edit_normalizes_selected_legacy_kind_only_after_submission(tmp_path: Path) -> None:
    store = tmp_path / "scheduler_tasks.json"
    legacy = ScheduledTask(
        id="legacy-loop",
        name="Original",
        kind=TaskKind.MANUAL_LOOP,
        cron="0 8 * * *",
        provider=Provider.INTERACTIVE_SHELL,
        params={LOOP_PROMPT_PARAM: "Inspect latency", "custom": "retained"},
    ).model_dump(mode="json")
    legacy["kind"] = "custom_investigation"
    store.write_text(json.dumps([legacy]), encoding="utf-8")
    before = store.read_bytes()
    [preview] = list_loop_summaries(store_path=store, read_only=True)
    assert preview.kind is TaskKind.MANUAL_LOOP and store.read_bytes() == before
    result, error = edit_loop(
        preview.task_ids, {"name": "Renamed"}, loop_id=preview.id, store_path=store
    )
    assert result is not None and not error
    [saved] = json.loads(store.read_text(encoding="utf-8"))
    assert saved["kind"] == TaskKind.MANUAL_LOOP.value and saved["name"] == "Renamed"
    assert saved["params"] == legacy["params"]
