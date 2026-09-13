"""Unit tests for work-item selector resolution and list ordering."""

from __future__ import annotations

import threading
from pathlib import Path

from core.domain.work_items.models import WorkItemPriority, WorkItemStatus
from core.domain.work_items.store import (
    add_work_item,
    complete_work_items,
    list_work_items,
    resolve_work_item_selector,
    update_work_item,
)


def _path(tmp_path: Path) -> Path:
    return tmp_path / "work_items.json"


def test_selector_prefers_id_over_title_substring(tmp_path: Path) -> None:
    path = _path(tmp_path)
    named = add_work_item(title="pay invoices", store_path=path)
    add_work_item(title="unrelated", store_path=path)
    by_id = resolve_work_item_selector(named.display_id, store_path=path)
    by_title = resolve_work_item_selector("pay invoices", store_path=path)
    assert by_id.item is not None and by_id.item.id == named.id
    assert by_title.item is not None and by_title.item.id == named.id


def test_selector_prefix_and_ambiguous_title(tmp_path: Path) -> None:
    path = _path(tmp_path)
    first = add_work_item(title="pay invoice", store_path=path)
    add_work_item(title="pay taxes", store_path=path)
    prefix = resolve_work_item_selector(first.id[:6], store_path=path)
    assert prefix.item is not None and prefix.item.id == first.id
    ambiguous = resolve_work_item_selector("pay", store_path=path)
    assert ambiguous.error == "ambiguous"
    assert len(ambiguous.candidates) == 2


def test_selector_empty_and_not_found(tmp_path: Path) -> None:
    path = _path(tmp_path)
    add_work_item(title="task", store_path=path)
    empty = resolve_work_item_selector("  #  ", store_path=path)
    missing = resolve_work_item_selector("no-such-item", store_path=path)
    assert empty.error == "empty_selector"
    assert missing.error == "not_found"


def test_list_orders_by_priority_then_created_and_completed_last(tmp_path: Path) -> None:
    path = _path(tmp_path)
    add_work_item(title="low", priority=WorkItemPriority.LOW, store_path=path)
    urgent = add_work_item(title="urgent", priority=WorkItemPriority.URGENT, store_path=path)
    add_work_item(title="normal", store_path=path)
    complete_work_items([urgent.id], store_path=path)
    titles = [item.title for item in list_work_items(status=None, store_path=path)]
    assert titles == ["normal", "low", "urgent"]


def test_update_rejects_empty_title(tmp_path: Path) -> None:
    path = _path(tmp_path)
    item = add_work_item(title="keep me", store_path=path)
    result = update_work_item(item.id, changes={"title": "   "}, store_path=path)
    assert result.error == "work item title cannot be empty"
    assert result.item is None
    listed = list_work_items(status=WorkItemStatus.OPEN, store_path=path)
    assert listed[0].title == "keep me"


def test_failed_after_save_rolls_back_before_concurrent_update(tmp_path: Path) -> None:
    path = _path(tmp_path)
    item = add_work_item(title="keep me", store_path=path)
    effect_started = threading.Event()
    release_effect = threading.Event()
    concurrent_started = threading.Event()
    concurrent_finished = threading.Event()
    failures: list[Exception] = []

    def _fail_after_save(_updated: object) -> None:
        effect_started.set()
        assert release_effect.wait(timeout=2)
        raise OSError("sync failed")

    def _failing_update() -> None:
        try:
            update_work_item(
                item.id,
                changes={"owner": "temporary"},
                store_path=path,
                after_save=_fail_after_save,
            )
        except Exception as exc:
            failures.append(exc)

    def _concurrent_update() -> None:
        concurrent_started.set()
        update_work_item(item.id, changes={"notes": "newer"}, store_path=path)
        concurrent_finished.set()

    failing_thread = threading.Thread(target=_failing_update)
    concurrent_thread = threading.Thread(target=_concurrent_update)
    failing_thread.start()
    assert effect_started.wait(timeout=2)
    concurrent_thread.start()
    assert concurrent_started.wait(timeout=2)
    assert not concurrent_finished.wait(timeout=0.1)
    release_effect.set()
    failing_thread.join(timeout=2)
    concurrent_thread.join(timeout=2)

    assert not failing_thread.is_alive()
    assert not concurrent_thread.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], OSError)
    stored = list_work_items(status=None, store_path=path)[0]
    assert stored.owner == ""
    assert stored.notes == "newer"
