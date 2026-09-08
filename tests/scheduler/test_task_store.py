"""Tests for the JSON-backed task store."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from infrastructure.scheduling.scheduler.storage.run_store import get_runs, try_claim
from infrastructure.scheduling.scheduler.storage.task_store import (
    _quarantine_unreadable,
    add_task,
    get_task,
    list_tasks,
    remove_task,
    update_task,
)
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind


@pytest.fixture()
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "scheduler_tasks.json"


def _db_path(store_path: Path) -> Path:
    return store_path.with_name("scheduler.db")


class TestStore:
    def test_list_empty(self, store_path: Path) -> None:
        tasks = list_tasks(store_path)
        assert tasks == []

    def test_add_and_list(self, store_path: Path) -> None:
        task = ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * 1-5",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )
        added = add_task(task, store_path)
        assert added.id == task.id

        tasks = list_tasks(store_path)
        assert len(tasks) == 1
        assert tasks[0].id == task.id
        assert tasks[0].kind == TaskKind.MANUAL_LOOP

    def test_get_task(self, store_path: Path) -> None:
        task = ScheduledTask(
            kind=TaskKind.SENTRY_MORNING_DIGEST,
            cron="0 8 * * 1",
            provider=Provider.SLACK,
            chat_id="C123",
        )
        add_task(task, store_path)

        found = get_task(task.id, store_path)
        assert found is not None
        assert found.kind == TaskKind.SENTRY_MORNING_DIGEST

    def test_get_task_not_found(self, store_path: Path) -> None:
        assert get_task("nonexistent", store_path) is None

    def test_remove_task(self, store_path: Path) -> None:
        task = ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )
        add_task(task, store_path)
        assert remove_task(task.id, store_path) is True
        assert list_tasks(store_path) == []

    def test_remove_task_cascade_deletes_runs(self, store_path: Path) -> None:
        """Removing a task must also remove its TaskRun records."""
        task = ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )
        add_task(task, store_path)

        db_path = _db_path(store_path)
        try_claim(task.id, "2026-01-01T09:00", db_path=db_path)
        try_claim(task.id, "2026-01-01T10:00", db_path=db_path)

        assert remove_task(task.id, store_path) is True

        assert get_runs(task.id, db_path=db_path) == []

    def test_remove_task_cascade_does_not_affect_other_tasks(self, store_path: Path) -> None:
        """Removing one task's runs must not delete another task's runs."""
        task_a = ScheduledTask(
            id="task-a",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )
        task_b = ScheduledTask(
            id="task-b",
            kind=TaskKind.SENTRY_MORNING_DIGEST,
            cron="0 8 * * 1",
            provider=Provider.SLACK,
            chat_id="C123",
        )
        add_task(task_a, store_path)
        add_task(task_b, store_path)

        db_path = _db_path(store_path)
        try_claim("task-a", "2026-01-01T09:00", db_path=db_path)
        try_claim("task-b", "2026-01-01T09:00", db_path=db_path)

        assert remove_task("task-a", store_path) is True

        assert get_runs("task-a", db_path=db_path) == []
        assert len(get_runs("task-b", db_path=db_path)) == 1

    def test_remove_nonexistent(self, store_path: Path) -> None:
        assert remove_task("nonexistent", store_path) is False

    def test_update_task(self, store_path: Path) -> None:
        task = ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )
        add_task(task, store_path)

        task.enabled = False
        assert update_task(task, store_path) is True

        updated = get_task(task.id, store_path)
        assert updated is not None
        assert updated.enabled is False

    def test_update_nonexistent(self, store_path: Path) -> None:
        task = ScheduledTask(
            id="nonexistent",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
        )
        assert update_task(task, store_path) is False

    def test_multiple_tasks(self, store_path: Path) -> None:
        for i in range(3):
            task = ScheduledTask(
                kind=TaskKind.MANUAL_LOOP,
                cron=f"{i} 9 * * *",
                provider=Provider.TELEGRAM,
                chat_id=f"-{i}",
            )
            add_task(task, store_path)

        tasks = list_tasks(store_path)
        assert len(tasks) == 3

    def test_corrupted_store_returns_empty(self, store_path: Path) -> None:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("not valid json", encoding="utf-8")
        tasks = list_tasks(store_path)
        assert tasks == []

    def test_store_with_invalid_entries_skips_them(self, store_path: Path) -> None:
        import json

        store_path.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {"id": "valid1", "kind": "manual_loop", "cron": "0 9 * * *", "provider": "telegram"},
            {"invalid": "entry"},
        ]
        store_path.write_text(json.dumps(data), encoding="utf-8")
        tasks = list_tasks(store_path)
        assert len(tasks) == 1
        assert tasks[0].id == "valid1"
        assert tasks[0].skill_name == ""
        assert tasks[0].skill_inputs == {}


class TestRecurringSkillStoreIdentity:
    def test_changed_skill_revision_updates_existing_schedule(self, store_path: Path) -> None:
        from core.agent_harness.prompts.skills.schedule import find_action_skill, skill_revision

        skill = find_action_skill("morning-report")
        assert skill is not None
        revision_a = skill_revision(skill)
        revision_b = "1" * 64
        base = {
            "kind": TaskKind.RECURRING_SKILL,
            "cron": "0 8 * * 1-5",
            "timezone": "UTC",
            "provider": Provider.SLACK,
            "chat_id": "C0123ABCD",
            "skill_name": "morning-report",
            "skill_inputs": {},
        }
        first = add_task(ScheduledTask(**base, skill_revision=revision_a), store_path)
        second = add_task(ScheduledTask(**base, skill_revision=revision_b), store_path)
        stored = list_tasks(store_path)
        assert first.id == second.id
        assert second.skill_revision == revision_b
        assert len(stored) == 1
        assert stored[0].skill_revision == revision_b


class TestAddTaskDeduplicates:
    """One confirmation, one schedule.

    A real install accumulated 37 byte-identical ``daily_summary`` rows because
    every confirmation inserted instead of matching an existing schedule.
    """

    @staticmethod
    def _daily_summary(**overrides: object) -> ScheduledTask:
        fields: dict[str, object] = {
            "kind": TaskKind.MANUAL_LOOP,
            "cron": "0 8 * * 1-5",
            "timezone": "UTC",
            "provider": Provider.SLACK,
            "chat_id": "C0123ABCD",
        }
        fields.update(overrides)
        return ScheduledTask(**fields)  # type: ignore[arg-type]

    def test_identical_task_is_stored_once(self, store_path: Path) -> None:
        # Arrange
        first = add_task(self._daily_summary(), store_path)

        # Act: the user confirms the same schedule again.
        second = add_task(self._daily_summary(), store_path)

        # Assert: one row, and the caller gets the schedule that already exists
        # so any id it reports back stays valid.
        assert len(list_tasks(store_path)) == 1
        assert second.id == first.id

    def test_repeated_confirmations_do_not_accumulate(self, store_path: Path) -> None:
        # Arrange / Act: the observed failure, in miniature.
        for _ in range(10):
            add_task(self._daily_summary(), store_path)

        # Assert
        assert len(list_tasks(store_path)) == 1

    def test_a_different_destination_is_a_different_schedule(self, store_path: Path) -> None:
        # Arrange / Act
        add_task(self._daily_summary(chat_id="C0000AAA"), store_path)
        add_task(self._daily_summary(chat_id="C1111BBB"), store_path)

        # Assert: merging these would silently drop a report the user wanted.
        assert len(list_tasks(store_path)) == 2

    def test_a_different_schedule_time_is_a_different_task(self, store_path: Path) -> None:
        # Arrange / Act
        add_task(self._daily_summary(cron="0 8 * * 1-5"), store_path)
        add_task(self._daily_summary(cron="0 18 * * 1-5"), store_path)

        # Assert
        assert len(list_tasks(store_path)) == 2

    def test_different_params_stay_separate(self, store_path: Path) -> None:
        # Arrange / Act: same slot, different report configuration.
        add_task(self._daily_summary(params={"stats_period": "7d"}), store_path)
        add_task(self._daily_summary(params={"stats_period": "30d"}), store_path)

        # Assert
        assert len(list_tasks(store_path)) == 2


class TestReloadSignal:
    """Store mutations wake a running scheduler, so every caller path benefits."""

    @staticmethod
    def _capture(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
        signals: list[bool] = []
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.reload_signal.request_scheduler_reload",
            lambda: signals.append(True),
        )
        return signals

    @staticmethod
    def _task() -> ScheduledTask:
        return ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

    def test_add_signals_reload(self, store_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        signals = self._capture(monkeypatch)
        add_task(self._task(), store_path)
        assert signals == [True]

    def test_duplicate_add_does_not_signal_again(
        self, store_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        signals = self._capture(monkeypatch)
        task = self._task()
        add_task(task, store_path)
        signals.clear()
        add_task(task, store_path)  # identical schedule → dedup, no change
        assert signals == []

    def test_remove_signals_reload(self, store_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        added = add_task(self._task(), store_path)
        signals = self._capture(monkeypatch)
        assert remove_task(added.id, store_path) is True
        assert signals == [True]

    def test_remove_missing_does_not_signal(
        self, store_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        signals = self._capture(monkeypatch)
        assert remove_task("does-not-exist", store_path) is False
        assert signals == []


class TestStoreSurvivesTornWrites:
    """A crash mid-write, or a store that will not parse, must not lose tasks."""

    @staticmethod
    def _digest(hour: int) -> ScheduledTask:
        return ScheduledTask(
            name=f"digest-{hour}",
            kind=TaskKind.SENTRY_MORNING_DIGEST,
            cron=f"0 {hour} * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )

    def test_save_never_truncates_the_live_file(self, store_path: Path) -> None:
        add_task(self._digest(7), store_path)
        before = store_path.read_text(encoding="utf-8")

        # A truncating writer would have already destroyed `before` by the
        # time this raises -- os.replace is the last step, after the temp
        # file is fully written and fsynced.
        def _explode(_src: object, _dst: object) -> None:
            raise OSError("crash during rename")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                "infrastructure.scheduling.scheduler.storage.task_store.os.replace", _explode
            )
            with pytest.raises(OSError):
                add_task(self._digest(8), store_path)

        assert store_path.read_text(encoding="utf-8") == before
        assert [task.name for task in list_tasks(store_path)] == ["digest-7"]
        assert list(store_path.parent.glob(f"{store_path.name}.tmp*")) == []

    def test_add_preserves_an_unreadable_store_instead_of_replacing_it(
        self, store_path: Path
    ) -> None:
        # A store torn in half, exactly as an interrupted write leaves it.
        for hour in (7, 8, 9):
            add_task(self._digest(hour), store_path)
        full = store_path.read_text(encoding="utf-8")
        store_path.write_text(full[: len(full) // 2], encoding="utf-8")

        add_task(self._digest(10), store_path)

        # The new task is stored, and the damaged file still exists under a
        # quarantine name rather than having been overwritten.
        assert [task.name for task in list_tasks(store_path)] == ["digest-10"]
        quarantined = list(store_path.parent.glob(f"{store_path.name}.corrupt-*"))
        assert len(quarantined) == 1
        assert quarantined[0].read_text(encoding="utf-8") == full[: len(full) // 2]

    def test_remove_and_update_leave_an_unreadable_store_untouched(self, store_path: Path) -> None:
        # Neither call finds its target in a store it cannot read, so
        # neither has any business rewriting -- or quarantining -- the file.
        task = self._digest(7)
        add_task(task, store_path)
        store_path.write_text("{ not json", encoding="utf-8")

        removed = remove_task(task.id, store_path)
        updated = update_task(task, store_path)

        assert removed is False
        assert updated is False
        assert store_path.read_text(encoding="utf-8") == "{ not json"

    def test_a_non_list_payload_is_treated_as_unreadable(self, store_path: Path) -> None:
        # Valid JSON of the wrong shape is just as unusable as broken JSON,
        # and previously fell through to the same silent-empty path.
        store_path.write_text('{"tasks": []}', encoding="utf-8")

        add_task(self._digest(7), store_path)

        assert [task.name for task in list_tasks(store_path)] == ["digest-7"]
        assert len(list(store_path.parent.glob(f"{store_path.name}.corrupt-*"))) == 1

    def test_two_corruptions_in_one_second_keep_both_recovery_copies(
        self, store_path: Path
    ) -> None:
        # Freeze the clock so both quarantines derive the same second -- a
        # name built from the timestamp alone would collide here, and the
        # second os.replace would erase the first casualty.
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                "infrastructure.scheduling.scheduler.storage.task_store.time.time",
                lambda: 1_700_000_000.0,
            )

            store_path.write_text("first torn write", encoding="utf-8")
            add_task(self._digest(7), store_path)
            store_path.write_text("second torn write", encoding="utf-8")
            add_task(self._digest(8), store_path)

        quarantined = sorted(store_path.parent.glob(f"{store_path.name}.corrupt-*"))
        assert len(quarantined) == 2
        assert {path.read_text(encoding="utf-8") for path in quarantined} == {
            "first torn write",
            "second torn write",
        }

    def test_save_fsyncs_the_parent_directory_after_replace(
        self, store_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The rename itself, not just the file's contents, must survive a
        power loss -- os.replace alone only guarantees the latter.
        """
        fsync_calls: list[int] = []
        real_fsync = os.fsync

        def _counting_fsync(fd: int) -> None:
            real_fsync(fd)
            fsync_calls.append(fd)

        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.storage.task_store.os.fsync", _counting_fsync
        )

        add_task(self._digest(7), store_path)

        # Two fsyncs per save: once for the temp file's contents, once for
        # the parent directory so the rename itself is durable too.
        assert len(fsync_calls) == 2

    def test_quarantine_removes_the_empty_aside_file_if_replace_fails(
        self, store_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed replace must not leave an empty file masquerading as
        the quarantined data.
        """
        store_path.write_text("not valid json", encoding="utf-8")

        def _explode(_src: object, _dst: object) -> None:
            raise OSError("simulated replace failure")

        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.storage.task_store.os.replace", _explode
        )

        with pytest.raises(OSError):
            _quarantine_unreadable(store_path)

        assert list(store_path.parent.glob(f"{store_path.name}.corrupt-*")) == []
        # The original (unreadable) store is untouched -- os.replace never happened.
        assert store_path.read_text(encoding="utf-8") == "not valid json"
