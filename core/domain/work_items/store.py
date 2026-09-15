"""JSON-backed CRUD for durable human work items."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from filelock import FileLock

from config.constants import OPENSRE_HOME_DIR, OPENSRE_WORK_ITEMS_DIR_ENV
from core.domain.work_items.files import (
    work_items_dir,
    work_items_path,
    write_text_atomically,
)
from core.domain.work_items.models import (
    WorkItem,
    WorkItemChannelTarget,
    WorkItemPriority,
    WorkItemStatus,
    WorkItemUpdates,
    make_work_item,
    mark_work_item_completed,
    parse_status,
    update_work_item_fields,
)
from core.domain.work_items.scoring import PRIORITY_WEIGHTS

logger = logging.getLogger(__name__)

_LOCK_FILENAME = ".work_items.lock"
_LEGACY_SLACK_FILENAME = "slack_captured_tasks.jsonl"
_LEGACY_IMPORT_SENTINEL = ".legacy_slack_imported"
_SELECTOR_PREFIX_MIN_LEN: Final[int] = 4


class WorkItemStoreError(RuntimeError):
    """Raised when the durable work-item store cannot be loaded safely."""


@dataclass(frozen=True)
class WorkItemResolution:
    selector: str
    item: WorkItem | None = None
    error: str = ""
    candidates: tuple[WorkItem, ...] = ()

    @property
    def ok(self) -> bool:
        return self.item is not None and not self.error


@dataclass(frozen=True)
class CompleteWorkItemsResult:
    completed: tuple[WorkItem, ...]
    already_completed: tuple[WorkItem, ...]
    not_found: tuple[str, ...]
    ambiguous: dict[str, tuple[WorkItem, ...]]

    @property
    def changed(self) -> bool:
        return bool(self.completed)


@dataclass(frozen=True)
class UpdateWorkItemResult:
    item: WorkItem | None = None
    error: str = ""
    candidates: tuple[WorkItem, ...] = ()

    @property
    def changed(self) -> bool:
        return self.item is not None and not self.error


def ensure_work_items_store(store_path: Path | None = None) -> Path:
    path = store_path or work_items_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        write_text_atomically(path, "[]\n")
    _import_legacy_slack_tasks_once(path)
    return path


def list_work_items(
    *,
    status: WorkItemStatus | str | None = WorkItemStatus.OPEN,
    project: str = "",
    owner: str = "",
    store_path: Path | None = None,
) -> list[WorkItem]:
    path = store_path or work_items_path()
    with _store_lock(path):
        _import_legacy_slack_tasks_once(path)
        items = _load_items_no_lock(path)
    filtered = _filter_items(items, status=status, project=project, owner=owner)
    filtered.sort(key=_sort_key)
    return filtered


def get_work_item(item_id: str, store_path: Path | None = None) -> WorkItem | None:
    clean = item_id.strip()
    if not clean:
        return None
    for item in list_work_items(status=None, store_path=store_path):
        if item.id == clean or item.display_id == clean:
            return item
    return None


def add_work_item(
    *,
    title: str,
    priority: WorkItemPriority | str = WorkItemPriority.NORMAL,
    project: str = "",
    owner: str = "",
    notes: str = "",
    due_at: str = "",
    remind_at: str = "",
    channel_provider: str = "",
    channel_id: str = "",
    channel_targets: Sequence[WorkItemChannelTarget] = (),
    source: str = "",
    created_by: str = "",
    store_path: Path | None = None,
) -> WorkItem:
    item = make_work_item(
        title=title,
        priority=priority,
        project=project,
        owner=owner,
        notes=notes,
        due_at=due_at,
        remind_at=remind_at,
        channel=WorkItemChannelTarget(
            provider=channel_provider.strip().lower(),
            chat_id=channel_id.strip(),
        ),
        channel_targets=channel_targets,
        source=source,
        created_by=created_by,
    )
    path = store_path or work_items_path()
    with _store_lock(path):
        _import_legacy_slack_tasks_once(path)
        items = _load_items_no_lock(path)
        items.append(item)
        _save_items_no_lock(path, items)
    return item


def resolve_work_item_selector(
    selector: str,
    *,
    items: Sequence[WorkItem] | None = None,
    store_path: Path | None = None,
) -> WorkItemResolution:
    clean = _clean_selector(selector)
    if not clean:
        return WorkItemResolution(selector=selector, error="empty_selector")
    candidates = tuple(
        items if items is not None else list_work_items(status=None, store_path=store_path)
    )
    return _resolve_selector_from_items(clean, candidates)


def complete_work_items(
    selectors: Sequence[str],
    *,
    store_path: Path | None = None,
) -> CompleteWorkItemsResult:
    path = store_path or work_items_path()
    completed: list[WorkItem] = []
    already_completed: list[WorkItem] = []
    not_found: list[str] = []
    ambiguous: dict[str, tuple[WorkItem, ...]] = {}

    with _store_lock(path):
        _import_legacy_slack_tasks_once(path)
        items = _load_items_no_lock(path)
        by_id = {item.id: item for item in items}
        for selector in selectors:
            resolution = _resolve_selector_from_items(
                _clean_selector(selector), tuple(by_id.values())
            )
            if resolution.item is None:
                if resolution.error == "ambiguous":
                    ambiguous[selector] = resolution.candidates
                else:
                    not_found.append(selector)
                continue
            if resolution.item.status is WorkItemStatus.COMPLETED:
                already_completed.append(resolution.item)
                continue
            updated = mark_work_item_completed(resolution.item)
            by_id[updated.id] = updated
            completed.append(updated)
        if completed:
            _save_items_no_lock(path, list(by_id.values()))

    return CompleteWorkItemsResult(
        completed=tuple(completed),
        already_completed=tuple(already_completed),
        not_found=tuple(not_found),
        ambiguous=ambiguous,
    )


def update_work_item(
    selector: str,
    *,
    changes: WorkItemUpdates,
    store_path: Path | None = None,
    after_save: Callable[[WorkItem], None] | None = None,
) -> UpdateWorkItemResult:
    """Update one item, rolling it back if ``after_save`` raises."""
    if not changes:
        return UpdateWorkItemResult(error="no_changes")
    path = store_path or work_items_path()
    with _store_lock(path):
        _import_legacy_slack_tasks_once(path)
        items = _load_items_no_lock(path)
        resolution = _resolve_selector_from_items(_clean_selector(selector), tuple(items))
        if resolution.item is None:
            return UpdateWorkItemResult(
                error=resolution.error or "not_found", candidates=resolution.candidates
            )
        try:
            updated = update_work_item_fields(resolution.item, changes)
        except ValueError as exc:
            return UpdateWorkItemResult(error=str(exc))
        saved = [updated if item.id == updated.id else item for item in items]
        _save_items_no_lock(path, saved)
        if after_save is not None:
            try:
                after_save(updated)
            except Exception:
                _save_items_no_lock(path, items)
                raise
        return UpdateWorkItemResult(item=updated)


def set_work_item_last_reminded(
    item_id: str,
    timestamp: str,
    *,
    store_path: Path | None = None,
) -> WorkItem | None:
    return update_work_item(
        item_id,
        changes={"last_reminded_at": timestamp},
        store_path=store_path,
    ).item


def _filter_items(
    items: Sequence[WorkItem],
    *,
    status: WorkItemStatus | str | None,
    project: str,
    owner: str,
) -> list[WorkItem]:
    normalized_project = project.strip().lower()
    normalized_owner = owner.strip().lower()
    normalized_status: WorkItemStatus | None = None
    if status not in (None, "", "all"):
        normalized_status = parse_status(status)
    result: list[WorkItem] = []
    for item in items:
        if normalized_status is not None and item.status is not normalized_status:
            continue
        if normalized_project and item.project.lower() != normalized_project:
            continue
        if normalized_owner and item.owner.lower() != normalized_owner:
            continue
        result.append(item)
    return result


def _sort_key(item: WorkItem) -> tuple[int, int, str]:
    status_order = 1 if item.status is WorkItemStatus.COMPLETED else 0
    return (status_order, -PRIORITY_WEIGHTS[item.priority], item.created_at)


def _match_id(item: WorkItem, clean: str) -> bool:
    return item.id == clean or item.display_id == clean


def _match_id_prefix(item: WorkItem, clean: str) -> bool:
    return len(clean) >= _SELECTOR_PREFIX_MIN_LEN and item.id.startswith(clean)


def _match_exact_title(item: WorkItem, clean: str) -> bool:
    return item.title.lower() == clean.lower()


def _match_title_contains(item: WorkItem, clean: str) -> bool:
    return clean.lower() in item.title.lower()


_SELECTOR_MATCHERS: Final[tuple[Callable[[WorkItem, str], bool], ...]] = (
    _match_id,
    _match_id_prefix,
    _match_exact_title,
    _match_title_contains,
)


def _unique_match(selector: str, matches: Sequence[WorkItem]) -> WorkItemResolution | None:
    if len(matches) == 1:
        return WorkItemResolution(selector=selector, item=matches[0])
    if len(matches) > 1:
        return WorkItemResolution(selector=selector, error="ambiguous", candidates=tuple(matches))
    return None


def _resolve_selector_from_items(selector: str, items: Sequence[WorkItem]) -> WorkItemResolution:
    clean = _clean_selector(selector)
    if not clean:
        return WorkItemResolution(selector=selector, error="empty_selector")
    for matches in _SELECTOR_MATCHERS:
        resolution = _unique_match(selector, [item for item in items if matches(item, clean)])
        if resolution is not None:
            return resolution
    return WorkItemResolution(selector=selector, error="not_found")


def _clean_selector(selector: str) -> str:
    return str(selector or "").strip().removeprefix("#").strip()


def _store_lock(path: Path) -> FileLock:
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(path.parent / _LOCK_FILENAME), timeout=10)


def _load_items_no_lock(path: Path) -> list[WorkItem]:
    """Load work items from ``path``.

    A missing file is treated as an empty store. An existing but unreadable,
    truncated, or non-list file raises ``WorkItemStoreError`` so mutations
    cannot overwrite durable state with an empty list.
    """
    if not path.exists():
        return []
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read work item store: %s", exc)
        raise WorkItemStoreError(f"Failed to read work item store at {path}") from exc
    if not isinstance(raw, list):
        raise WorkItemStoreError(f"Work item store at {path} has a non-list root; refusing to load")
    items: list[WorkItem] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        try:
            items.append(WorkItem.from_mapping(entry))
        except ValueError as exc:
            logger.warning("Skipping invalid work item entry: %s", exc)
    return items


def _save_items_no_lock(path: Path, items: Sequence[WorkItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = [item.to_dict() for item in items]
    write_text_atomically(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _import_legacy_slack_tasks_once(path: Path) -> int:
    if store_path_uses_explicit_override():
        return 0
    if path != work_items_path():
        return 0

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    sentinel = path.parent / _LEGACY_IMPORT_SENTINEL
    if sentinel.exists():
        return 0

    legacy_path = OPENSRE_HOME_DIR / _LEGACY_SLACK_FILENAME
    if not legacy_path.exists():
        write_text_atomically(sentinel, "no legacy slack task store found\n")
        return 0

    items = _load_items_no_lock(path)
    existing_keys = {
        (item.title.lower(), item.created_at, item.created_by, item.channel.chat_id)
        for item in items
    }
    imported: list[WorkItem] = []
    try:
        lines = legacy_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("Failed to read legacy Slack task store: %s", exc)
        return 0

    for line in lines:
        try:
            raw: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, Mapping):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        item = make_work_item(
            title=text,
            priority=WorkItemPriority.NORMAL,
            source="slack_capture_task_legacy",
            created_by=str(raw.get("requester") or "").strip(),
            channel=WorkItemChannelTarget(
                provider="slack" if str(raw.get("channel_id") or "").strip() else "",
                chat_id=str(raw.get("channel_id") or "").strip(),
            ),
        )
        created_at = str(raw.get("created_at") or "").strip()
        if created_at:
            status_value = raw.get("status", WorkItemStatus.OPEN.value)
            item = update_work_item_fields(
                item,
                {
                    "status": str(status_value)
                    if status_value is not None
                    else WorkItemStatus.OPEN.value
                },
            )
            payload = item.to_dict()
            payload.update(
                {
                    "created_at": created_at,
                    "updated_at": created_at,
                    "completed_at": created_at if item.status is WorkItemStatus.COMPLETED else "",
                }
            )
            item = WorkItem.from_mapping(payload)
        key = (item.title.lower(), item.created_at, item.created_by, item.channel.chat_id)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        imported.append(item)

    if imported:
        _save_items_no_lock(path, [*items, *imported])
    write_text_atomically(sentinel, f"imported {len(imported)} legacy slack task(s)\n")
    return len(imported)


def store_path_uses_explicit_override() -> bool:
    return bool(os.getenv(OPENSRE_WORK_ITEMS_DIR_ENV, "").strip())


__all__ = [
    "CompleteWorkItemsResult",
    "UpdateWorkItemResult",
    "WorkItemResolution",
    "WorkItemStoreError",
    "add_work_item",
    "complete_work_items",
    "ensure_work_items_store",
    "get_work_item",
    "list_work_items",
    "resolve_work_item_selector",
    "set_work_item_last_reminded",
    "store_path_uses_explicit_override",
    "update_work_item",
    "work_items_dir",
    "work_items_path",
]
