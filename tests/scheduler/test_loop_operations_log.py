"""Operations-log coverage for scheduled loop lifecycle changes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.constants import OPENSRE_OPERATIONS_LOG_PATH_ENV
from infrastructure.observability.operations_log import read_operations
from infrastructure.scheduling.scheduler.loops import (
    create_manual_loop,
    delete_loop,
    set_loop_enabled,
)


def test_manual_loop_lifecycle_is_logged_without_prompt_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "operations.jsonl"
    store_path = tmp_path / "scheduler_tasks.json"
    prompt = "Check the private incident queue and summarize production risk"
    monkeypatch.setenv(OPENSRE_OPERATIONS_LOG_PATH_ENV, str(log_path))

    created = create_manual_loop(
        name="",
        prompt=prompt,
        cron="0 8 * * 1-5",
        channels=["interactive_shell"],
        store_path=store_path,
    )
    stopped, stop_error = set_loop_enabled(created.task.id, enabled=False, store_path=store_path)
    deleted, delete_error = delete_loop(created.task.id, store_path=store_path)

    assert stopped is not None, stop_error
    assert deleted is not None, delete_error
    records = read_operations(path=log_path)
    events = [record["event"] for record in records]
    assert events == [
        "scheduled_loop_created",
        "scheduled_loop_stopped",
        "scheduled_loop_deleted",
    ]
    assert records[0]["data"]["prompt_chars"] == len(prompt)
    assert records[0]["data"]["name"] == "[prompt-derived]"
    assert records[0]["data"]["channels"] == ["interactive_shell"]
    assert records[1]["data"]["enabled"] is False
    assert records[2]["data"]["task_ids"] == [created.task.id]
    assert prompt not in json.dumps(records)
