"""Tests for REPL task registry and /tasks · /cancel."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from infrastructure.scheduling.task_registry import TaskRegistry
from infrastructure.scheduling.task_types import TaskKind, TaskStatus
from surfaces.interactive_shell.command_registry import dispatch_slash
from surfaces.interactive_shell.session import (
    Session,
)


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


class TestTaskRecord:
    def test_lifecycle_completed(self) -> None:
        reg = TaskRegistry()
        t = reg.create(TaskKind.CLI_COMMAND)
        assert t.status == TaskStatus.PENDING
        t.mark_running()
        assert t.status == TaskStatus.RUNNING
        t.mark_completed(result="done")
        assert t.status == TaskStatus.COMPLETED
        assert t.result == "done"
        assert t.ended_at is not None
        t.mark_failed("x")
        assert t.status == TaskStatus.COMPLETED

    def test_mark_cancelled_idempotent_after_terminal(self) -> None:
        reg = TaskRegistry()
        t = reg.create(TaskKind.CLI_COMMAND)
        t.mark_running()
        t.mark_cancelled()
        assert t.status == TaskStatus.CANCELLED
        t.mark_completed(result="nope")
        assert t.status == TaskStatus.CANCELLED

    def test_request_cancel_sets_event_even_when_pending(self) -> None:
        reg = TaskRegistry()
        t = reg.create(TaskKind.CLI_COMMAND)
        assert t.request_cancel() is False
        assert t.cancel_requested.is_set()
        assert t.status == TaskStatus.PENDING

    def test_request_cancel_sets_event_and_terminates_process(self) -> None:
        reg = TaskRegistry()
        t = reg.create(TaskKind.CLI_COMMAND)
        t.mark_running()
        proc = MagicMock()
        proc.poll.return_value = None
        t.attach_process(proc)
        assert t.request_cancel() is True
        proc.terminate.assert_called_once()
        assert t.cancel_requested.is_set()


class TestTaskRegistry:
    def test_get_single_prefix_match(self) -> None:
        reg = TaskRegistry()
        t = reg.create(TaskKind.CLI_COMMAND)
        assert reg.get(t.task_id[:4]) == t
        assert reg.get("") is None

    def test_candidates_ambiguous_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _ids = iter(["11111111", "11112222"])

        def _fake_hex(_nbytes: int) -> str:
            return next(_ids)

        monkeypatch.setattr("infrastructure.scheduling.task_registry.secrets.token_hex", _fake_hex)
        session = Session()
        session.task_registry.create(TaskKind.CLI_COMMAND)
        session.task_registry.create(TaskKind.CLI_COMMAND)
        console, buf = _capture()
        dispatch_slash("/cancel 1111", session, console)
        assert "ambiguous" in buf.getvalue().lower()

    def test_ring_buffer_drops_oldest(self) -> None:
        reg = TaskRegistry(max_tasks=3)
        first = reg.create(TaskKind.CLI_COMMAND)
        reg.create(TaskKind.CLI_COMMAND)
        reg.create(TaskKind.CLI_COMMAND)
        reg.create(TaskKind.CLI_COMMAND)
        recent_ids = [t.task_id for t in reg.list_recent(10)]
        assert first.task_id not in recent_ids
        assert len(recent_ids) == 3

    def test_persistent_registry_reloads_running_pid(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
        reg = TaskRegistry.persistent()
        task = reg.create(TaskKind.CLI_COMMAND, command="opensre health")
        task.mark_running()
        task.attach_pid(os.getpid())

        reloaded = TaskRegistry.persistent()
        [loaded] = reloaded.list_recent()
        assert loaded.task_id == task.task_id
        assert loaded.status == TaskStatus.RUNNING
        assert loaded.pid == os.getpid()
        assert loaded.command == "opensre health"

    def test_persistent_registry_marks_missing_pid_finished(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
        store_path = tmp_path / "interactive_tasks.json"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(
            json.dumps(
                [
                    {
                        "task_id": "abc12345",
                        "kind": "cli_command",
                        "status": "running",
                        "started_at": 1.0,
                        "ended_at": None,
                        "result": None,
                        "error": None,
                        "pid": 999_999,
                        "command": "opensre health",
                    }
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.task_types.os.kill",
            lambda _pid, _sig: (_ for _ in ()).throw(ProcessLookupError()),
        )

        reloaded = TaskRegistry.persistent()
        [loaded] = reloaded.list_recent()
        assert loaded.status == TaskStatus.COMPLETED
        assert loaded.result == "process exited while shell was closed"

    def test_persistent_registry_ignores_retired_task_kinds(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
        store_path = tmp_path / "interactive_tasks.json"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(
            json.dumps(
                [
                    {
                        "task_id": "abc12345",
                        "kind": "retired_kind",
                        "status": "completed",
                        "started_at": 1.0,
                    }
                ]
            ),
            encoding="utf-8",
        )

        assert TaskRegistry.persistent().list_recent() == []

    def test_cancel_rehydrated_task_does_not_signal_pid(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
        calls: list[tuple[int, int]] = []

        def _fake_kill(pid: int, sig: int) -> None:
            calls.append((pid, sig))

        monkeypatch.setattr("infrastructure.scheduling.task_types.os.kill", _fake_kill)
        reg = TaskRegistry.persistent()
        task = reg.create(TaskKind.CLI_COMMAND, command="opensre health")
        task.mark_running()
        task.attach_pid(12345)

        reloaded = TaskRegistry.persistent()
        loaded = reloaded.get(task.task_id)
        assert loaded is not None
        assert loaded.request_cancel() is True
        assert loaded.status == TaskStatus.CANCELLED
        assert (12345, 15) not in calls

    def test_session_new_does_not_truncate_persistent_task_store(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
        session = Session()
        session.task_registry = TaskRegistry.persistent()
        task = session.task_registry.create(TaskKind.CLI_COMMAND, command="opensre health")
        task.mark_running()
        task.mark_completed(result="ok")

        session.clear()

        # /new must keep the on-disk store intact: a fresh persistent registry
        # still finds the task, and the session's swapped-in registry continues
        # to surface persisted history via its disk-backed merge so /tasks does
        # not "forget" the user's prior runs after /new.
        reloaded = TaskRegistry.persistent()
        [loaded] = reloaded.list_recent()
        assert loaded.task_id == task.task_id
        assert loaded.command == "opensre health"
        [visible_after_new] = session.task_registry.list_recent()
        assert visible_after_new.task_id == task.task_id


class TestSlashTaskCommands:
    def test_tasks_empty_message(self) -> None:
        session = Session()
        console, buf = _capture()
        dispatch_slash("/tasks", session, console)
        assert "no tasks" in buf.getvalue().lower()

    def test_tasks_shows_recent_rows(self) -> None:
        session = Session()
        t = session.task_registry.create(TaskKind.CLI_COMMAND)
        t.mark_running()
        t.mark_completed(result="rc")
        console, buf = _capture()
        dispatch_slash("/tasks", session, console)
        out = buf.getvalue()
        assert t.task_id in out
        assert "cli_command" in out
        assert "completed" in out.lower()

    def test_cancel_usage_without_id(self) -> None:
        session = Session()
        console, buf = _capture()
        dispatch_slash("/cancel", session, console)
        assert "usage" in buf.getvalue().lower()

    def test_cancel_unknown_id(self) -> None:
        session = Session()
        console, buf = _capture()
        dispatch_slash("/cancel deadbeef", session, console)
        assert "no task" in buf.getvalue().lower()

    def test_cancel_completed_task_message(self) -> None:
        session = Session()
        t = session.task_registry.create(TaskKind.CLI_COMMAND)
        t.mark_running()
        t.mark_completed(result="x")
        console, buf = _capture()
        dispatch_slash(f"/cancel {t.task_id}", session, console)
        assert "already finished" in buf.getvalue().lower()

    def test_cancel_running_task_signals_and_terminates_process(self) -> None:
        session = Session()
        t = session.task_registry.create(TaskKind.CLI_COMMAND)
        t.mark_running()
        proc = MagicMock()
        proc.poll.return_value = None
        t.attach_process(proc)
        console, buf = _capture()
        dispatch_slash(f"/cancel {t.task_id}", session, console)
        assert t.cancel_requested.is_set()
        proc.terminate.assert_called_once()
        out = buf.getvalue()
        assert "stop requested" in out.lower()
        assert t.task_id in out
