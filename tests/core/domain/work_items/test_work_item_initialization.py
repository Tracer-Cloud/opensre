"""Store initialization must serialize with concurrent work-item mutations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from filelock import FileLock, Timeout

from core.domain.work_items import (
    WorkItem,
    add_work_item,
    ensure_work_items_store,
    list_work_items,
    store,
)


def test_initialization_preserves_a_concurrent_add(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "work_items.json"
    original_lock = store._store_lock
    original_write = store.write_text_atomically
    attempts: list[WorkItem | None] = []

    def nonblocking_lock(target: Path) -> FileLock:
        lock = original_lock(target)
        lock.timeout = 0
        return lock

    def competing_add() -> WorkItem | None:
        try:
            return add_work_item(title="Keep this task", store_path=path)
        except Timeout:
            # The initializer holds the real store lock; retry after it finishes.
            return None

    def initialize_with_competing_add(target: Path, text: str) -> None:
        if target == path and text == "[]\n":
            # Run the writer after the absence check and before the initial rename.
            # A nonblocking lock makes the two possible outcomes deterministic.
            with ThreadPoolExecutor(max_workers=1) as executor:
                attempts.append(executor.submit(competing_add).result(timeout=5))
        original_write(target, text)

    monkeypatch.setattr(store, "_store_lock", nonblocking_lock)
    monkeypatch.setattr(store, "write_text_atomically", initialize_with_competing_add)
    assert ensure_work_items_store(path) == path
    assert len(attempts) == 1
    added = attempts[0]
    if added is None:
        added = add_work_item(title="Keep this task", store_path=path)
    assert list_work_items(status=None, store_path=path) == [added]
