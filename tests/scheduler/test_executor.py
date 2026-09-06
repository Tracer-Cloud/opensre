"""Tests for the task executor with isolated stores.

Delivery is inverted behind a per-provider adapter bundle. Routing/orchestration
tests install a fake bundle and configure each provider's outcome; adapter
behavior (creds resolution, the vendor transport call) is exercised by installing
the real bundle and patching that vendor's ``scheduled_delivery`` adapter.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

import infrastructure.scheduling.scheduler.delivery_bundle as delivery_bundle
from config.constants import OPENSRE_OPERATIONS_LOG_PATH_ENV
from infrastructure.observability.operations_log import read_operations
from infrastructure.scheduling.scheduler.executor import execute_task
from infrastructure.scheduling.scheduler.local_delivery import get_loop_messages
from infrastructure.scheduling.scheduler.loop_constants import LOOP_CHANNELS_PARAM
from infrastructure.scheduling.scheduler.runner import _recover_expired_tasks
from infrastructure.scheduling.scheduler.storage.run_store import get_runs
from infrastructure.scheduling.scheduler.storage.task_store import add_task
from infrastructure.scheduling.scheduler.types import (
    Provider,
    ScheduledTask,
    TaskKind,
    TaskStatus,
)
from tests.scheduler._bundle import real_runners

#: Generous enough to survive a loaded CI shard; a real hang still fails fast.
_SYNC_TIMEOUT_SECONDS = 15.0

_DELIVERY_PROVIDERS = (
    Provider.TELEGRAM,
    Provider.SLACK,
    Provider.DISCORD,
    Provider.ROCKETCHAT,
    Provider.INTERACTIVE_SHELL,
)


class _FakeAdapter:
    """Records the (task, message) it is handed and returns a fixed outcome."""

    def __init__(self) -> None:
        self.result: tuple[bool, str, str] = (True, "", "msg")
        self.calls: list[tuple[ScheduledTask, str]] = []

    def deliver(self, task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        self.calls.append((task, message))
        return self.result


class _CrashOnceAdapter:
    """Raises once to simulate a worker dying in the delivery call."""

    def __init__(self) -> None:
        self.crashed = False
        self.calls = 0

    def deliver(self, _task: ScheduledTask, _message: str) -> tuple[bool, str, str]:
        self.calls += 1
        if not self.crashed:
            self.crashed = True
            raise KeyboardInterrupt
        return True, "", "recovered-message"


def _install_fake_bundle() -> dict[Provider, _FakeAdapter]:
    """Install a fake adapter for every provider; return them to configure/inspect."""
    adapters = {provider: _FakeAdapter() for provider in _DELIVERY_PROVIDERS}
    delivery_bundle.ScheduledDeliveryAdapters(adapters).install()
    return adapters


def _install_real_bundle() -> None:
    """Install the production adapter bundle (exercises the real vendor adapters)."""
    from bootstrap.adapters import scheduled_delivery_adapters

    scheduled_delivery_adapters().install()


@pytest.fixture(autouse=True)
def _reset_delivery_bundle() -> None:
    """Drop the installed bundle after each test so it cannot leak across tests."""
    yield
    delivery_bundle._installed = None


@pytest.fixture()
def _tmp_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point both stores at tmp_path so tests are isolated."""
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.database.default_run_database_path",
        lambda: tmp_path / "scheduler.db",
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
        lambda: tmp_path / "tasks.json",
    )


def _expire_claim(db_path: Path, task_id: str, fire_time: str) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE task_runs SET lease_expires_at = ? WHERE task_id = ? AND fire_time = ?",
        ("2020-01-01T00:00:00+00:00", task_id, fire_time),
    )
    conn.commit()
    conn.close()


@pytest.mark.usefixtures("_tmp_stores")
class TestExecutor:
    @pytest.mark.parametrize("delivery_succeeds", [True, False])
    def test_recovered_one_shot_finalizes_only_after_success(
        self, tmp_path: Path, delivery_succeeds: bool
    ) -> None:
        from infrastructure.scheduling.scheduler.storage import get_task, try_claim

        task = add_task(
            ScheduledTask(
                kind=TaskKind.MANUAL_LOOP,
                cron="0 9 * * *",
                provider=Provider.SLACK,
                params={"disable_after_success": "true"},
            )
        )
        assert try_claim(task.id, "old-tick") is not None
        _expire_claim(tmp_path / "scheduler.db", task.id, "old-tick")
        adapters = _install_fake_bundle()
        adapters[Provider.SLACK].result = (
            (True, "", "message") if delivery_succeeds else (False, "unavailable", "")
        )
        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message", return_value="report"
        ):
            _recover_expired_tasks(real_runners())
        stored = get_task(task.id)
        assert stored is not None
        assert stored.enabled is not delivery_succeeds
        assert (stored.last_run is not None) is delivery_succeeds
        assert get_runs(task.id)[0].status is (
            TaskStatus.SUCCESS if delivery_succeeds else TaskStatus.FAILED
        )

    @pytest.mark.parametrize("ineligible", ["disabled", "missing", "filtered"])
    def test_recovery_skips_ineligible_claims_before_limiting(
        self, tmp_path: Path, ineligible: str
    ) -> None:
        from infrastructure.scheduling.scheduler.storage import get_task, try_claim

        blocked = ScheduledTask(
            id="blocked",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            enabled=ineligible != "disabled",
            chat_id="blocked",
        )
        if ineligible != "missing":
            add_task(blocked)
        task = add_task(
            ScheduledTask(
                kind=TaskKind.MANUAL_LOOP,
                cron="0 9 * * *",
                provider=Provider.SLACK,
                chat_id="eligible",
            )
        )
        for index in range(100):
            assert try_claim(blocked.id, f"old-{index}") is not None
        assert try_claim(task.id, "eligible-tick") is not None
        with sqlite3.connect(tmp_path / "scheduler.db") as conn:
            conn.execute(
                "UPDATE task_runs SET lease_expires_at = ? WHERE task_id = ?",
                ("2020-01-01T00:00:00+00:00", blocked.id),
            )
            conn.execute(
                "UPDATE task_runs SET lease_expires_at = ? WHERE task_id = ?",
                ("2021-01-01T00:00:00+00:00", task.id),
            )
        adapters = _install_fake_bundle()

        def accepts_task(candidate: ScheduledTask) -> bool:
            return candidate.provider is Provider.SLACK

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message", return_value="report"
        ):
            _recover_expired_tasks(
                real_runners(), task_filter=accepts_task if ineligible == "filtered" else None
            )
        assert len(adapters[Provider.SLACK].calls) == 1
        assert not adapters[Provider.TELEGRAM].calls
        assert get_runs(task.id)[0].status is TaskStatus.SUCCESS
        assert all(run.status is TaskStatus.RUNNING for run in get_runs(blocked.id, limit=100))
        stored = get_task(task.id)
        assert stored is not None and stored.last_run is not None

    @pytest.mark.parametrize("mutation", ["pause_and_edit", "delete"])
    def test_completion_preserves_concurrent_task_changes(self, mutation: str) -> None:
        from concurrent.futures import ThreadPoolExecutor

        from infrastructure.scheduling.scheduler.runner import _scheduled_job
        from infrastructure.scheduling.scheduler.storage import get_task, remove_task, update_task

        task = add_task(
            ScheduledTask(
                kind=TaskKind.MANUAL_LOOP,
                cron="0 9 * * *",
                provider=Provider.SLACK,
                chat_id="original",
            )
        )
        started = threading.Event()
        release = threading.Event()

        def build_while_editing(*_args: object) -> str:
            started.set()
            assert release.wait(_SYNC_TIMEOUT_SECONDS)
            return "report"

        _install_fake_bundle()
        with (
            patch(
                "infrastructure.scheduling.scheduler.executor.build_message", build_while_editing
            ),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            future = pool.submit(_scheduled_job, task.id, real_runners())
            try:
                assert started.wait(_SYNC_TIMEOUT_SECONDS)
                if mutation == "delete":
                    assert remove_task(task.id)
                else:
                    edited = get_task(task.id)
                    assert edited is not None
                    edited.enabled = False
                    edited.chat_id = "edited"
                    edited.cron = "0 10 * * *"
                    edited.params = {"loop_prompt": "new prompt"}
                    assert update_task(edited)
            finally:
                release.set()
            future.result(timeout=_SYNC_TIMEOUT_SECONDS)
        stored = get_task(task.id)
        if mutation == "delete":
            assert stored is None
        else:
            assert stored is not None
            assert stored.enabled is False
            assert stored.chat_id == "edited"
            assert stored.cron == "0 10 * * *"
            assert stored.params == {"loop_prompt": "new prompt"}
            assert stored.last_run is not None

    def test_failed_only_retry_retains_scope_after_crash(self, tmp_path: Path) -> None:
        from infrastructure.scheduling.scheduler.runner import run_task_now

        task = add_task(
            ScheduledTask(
                kind=TaskKind.MANUAL_LOOP,
                cron="0 9 * * *",
                provider=Provider.SLACK,
                params={
                    "delivery_targets": json.dumps(
                        [
                            {"provider": "slack", "chat_id": "C123"},
                            {"provider": "telegram", "chat_id": "456"},
                        ]
                    )
                },
            )
        )
        adapters = _install_fake_bundle()
        adapters[Provider.TELEGRAM].result = (False, "unavailable", "")
        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message", return_value="report"
        ):
            assert execute_task(task, "2026-01-01T09:00Z", real_runners())
        assert len(adapters[Provider.SLACK].calls) == 1
        with (
            patch(
                "infrastructure.scheduling.scheduler.executor.build_message",
                side_effect=KeyboardInterrupt,
            ),
            pytest.raises(KeyboardInterrupt),
        ):
            run_task_now(task.id, real_runners(), only_failed=True)
        crashed = get_runs(task.id)[0]
        assert crashed.status is TaskStatus.RUNNING
        _expire_claim(tmp_path / "scheduler.db", task.id, crashed.fire_time)
        adapters[Provider.TELEGRAM].calls.clear()
        adapters[Provider.TELEGRAM].result = (True, "", "recovered")
        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message", return_value="report"
        ):
            _recover_expired_tasks(real_runners())
        assert len(adapters[Provider.SLACK].calls) == 1
        assert len(adapters[Provider.TELEGRAM].calls) == 1
        recovered = get_runs(task.id)[0]
        assert recovered.attempt == 2
        assert recovered.status is TaskStatus.SUCCESS
        assert [(target.provider, target.chat_id) for target in recovered.targets] == [
            (Provider.TELEGRAM, "456")
        ]

    def test_crash_before_build_is_recovered_by_a_new_attempt(self, tmp_path: Path) -> None:
        from infrastructure.scheduling.scheduler.storage.run_store import get_runs, try_claim

        task = ScheduledTask(
            id="test_build_crash",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.SLACK,
            chat_id="C123",
        )
        fire_time = "2026-01-01T09:00"
        assert try_claim(task.id, fire_time, db_path=tmp_path / "scheduler.db") is not None
        _expire_claim(tmp_path / "scheduler.db", task.id, fire_time)
        adapters = _install_fake_bundle()

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            assert execute_task(task, fire_time, real_runners()) is True

        runs = get_runs(task.id)
        assert [run.status for run in runs] == [TaskStatus.SUCCESS, TaskStatus.ABANDONED]
        assert runs[0].attempt == 2
        assert len(adapters[Provider.SLACK].calls) == 1

    def test_crash_during_delivery_is_recovered_by_a_new_attempt(self, tmp_path: Path) -> None:
        from infrastructure.scheduling.scheduler.storage.run_store import get_runs

        adapter = _CrashOnceAdapter()
        _install_bundle({Provider.SLACK: adapter})
        task = ScheduledTask(
            id="test_delivery_crash",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.SLACK,
            chat_id="C123",
        )
        fire_time = "2026-01-01T09:00"

        with (
            patch(
                "infrastructure.scheduling.scheduler.executor.build_message",
                return_value="Scheduled report",
            ),
            pytest.raises(KeyboardInterrupt),
        ):
            execute_task(task, fire_time, real_runners())

        _expire_claim(tmp_path / "scheduler.db", task.id, fire_time)

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            assert execute_task(task, fire_time, real_runners()) is True

        runs = get_runs(task.id)
        assert [run.status for run in runs] == [TaskStatus.SUCCESS, TaskStatus.ABANDONED]
        assert runs[0].attempt == 2
        assert adapter.calls == 2

    def test_scheduler_recovery_sweep_resubmits_the_original_fire_time(
        self, tmp_path: Path
    ) -> None:
        from infrastructure.scheduling.scheduler.storage.run_store import get_runs, try_claim

        task = ScheduledTask(
            id="test_sweep_recovery",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.SLACK,
            chat_id="C123",
        )
        fire_time = "2026-01-01T09:00"
        add_task(task, tmp_path / "tasks.json")
        assert try_claim(task.id, fire_time, db_path=tmp_path / "scheduler.db") is not None
        _expire_claim(tmp_path / "scheduler.db", task.id, fire_time)
        adapters = _install_fake_bundle()

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            _recover_expired_tasks(real_runners())

        runs = get_runs(task.id)
        assert [run.status for run in runs] == [TaskStatus.SUCCESS, TaskStatus.ABANDONED]
        assert runs[0].attempt == 2
        assert len(adapters[Provider.SLACK].calls) == 1

    def test_telegram_delivery_success(self) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.TELEGRAM].result = (True, "", "msg_42")
        task = ScheduledTask(
            id="test_tg_01",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        assert len(adapters[Provider.TELEGRAM].calls) == 1

    def test_telegram_missing_credentials(self) -> None:
        _install_real_bundle()
        task = ScheduledTask(
            id="test_tg_02",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with (
            patch(
                "infrastructure.scheduling.scheduler.executor.build_message",
                return_value="Scheduled report",
            ),
            patch(
                "integrations.telegram.scheduled_delivery.resolve_telegram_credentials",
                return_value={},
            ),
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is False

    def test_slack_delivery_success(self) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.SLACK].result = (True, "", "ts_123")
        task = ScheduledTask(
            id="test_sl_01",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.SLACK,
            chat_id="C123456",
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        assert len(adapters[Provider.SLACK].calls) == 1

    def test_discord_delivery_success(self) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.DISCORD].result = (True, "", "msg_99")
        task = ScheduledTask(
            id="test_dc_01",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.DISCORD,
            chat_id="123456789",
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        assert len(adapters[Provider.DISCORD].calls) == 1

    def test_rocketchat_delivery_success(self) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.ROCKETCHAT].result = (True, "", "msg_rc")
        task = ScheduledTask(
            id="test_rc_01",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.ROCKETCHAT,
            chat_id="#ops",
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        assert len(adapters[Provider.ROCKETCHAT].calls) == 1

    def test_interactive_shell_delivery_success(self, tmp_path: Path) -> None:
        _install_real_bundle()
        inbox_path = tmp_path / "loop_messages.jsonl"
        task = ScheduledTask(
            id="test_shell_01",
            name="Local loop",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.INTERACTIVE_SHELL,
        )

        with (
            patch(
                "infrastructure.scheduling.scheduler.executor.build_message",
                return_value="<b>Scheduled</b> report",
            ),
            patch(
                "infrastructure.scheduling.scheduler.local_delivery._default_inbox_path",
                return_value=inbox_path,
            ),
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        messages = get_loop_messages(inbox_path=inbox_path)
        assert len(messages) == 1
        assert messages[0].name == "Local loop"
        assert messages[0].message == "Scheduled report"

    def test_execution_logs_operations_without_message_body(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.INTERACTIVE_SHELL].result = (True, "", "local:1")
        log_path = tmp_path / "operations.jsonl"
        monkeypatch.setenv(OPENSRE_OPERATIONS_LOG_PATH_ENV, str(log_path))
        task = ScheduledTask(
            id="test_ops_log",
            name="Local loop",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.INTERACTIVE_SHELL,
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Sensitive scheduled report body",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        records = read_operations(path=log_path)
        events = [record["event"] for record in records]
        assert events == [
            "scheduled_task_execution_started",
            "scheduled_task_execution_completed",
        ]
        completed = records[-1]["data"]
        assert completed["task_id"] == "test_ops_log"
        assert completed["fire_time"] == "2026-01-01T09:00"
        assert completed["status"] == "success"
        assert completed["message_chars"] == len("Sensitive scheduled report body")
        assert completed["message_id"] == "local:1"
        assert "Sensitive scheduled report body" not in json.dumps(records)

    def test_loop_fanout_builds_message_once(self) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.INTERACTIVE_SHELL].result = (True, "", "local:1")
        adapters[Provider.SLACK].result = (True, "", "ts_123")
        task = ScheduledTask(
            id="test_fanout",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_CHANNELS_PARAM: "interactive_shell,slack"},
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ) as mock_build:
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        mock_build.assert_called_once()
        assert len(adapters[Provider.INTERACTIVE_SHELL].calls) == 1
        assert len(adapters[Provider.SLACK].calls) == 1

    def test_loop_fanout_partial_success_completes_claim(self) -> None:
        """One channel failing must not leave an unrecoverable failed claim."""
        from infrastructure.scheduling.scheduler.storage.run_store import get_runs

        adapters = _install_fake_bundle()
        adapters[Provider.INTERACTIVE_SHELL].result = (True, "", "local:1")
        adapters[Provider.SLACK].result = (False, "webhook missing", "")
        task = ScheduledTask(
            id="test_fanout_partial",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_CHANNELS_PARAM: "interactive_shell,slack"},
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:05", real_runners())

        assert result is True
        runs = get_runs(task.id)
        assert len(runs) == 1
        assert runs[0].status.value == "success"
        assert "interactive_shell:local:1" in runs[0].posted_message_id
        assert "partial delivery" in runs[0].error
        assert "slack" in runs[0].error
        # First attempt + two retries for the failed slack destination.
        assert len(adapters[Provider.SLACK].calls) == 3
        assert len(adapters[Provider.INTERACTIVE_SHELL].calls) == 1

    def test_rocketchat_delivery_posts_to_channel(self) -> None:
        _install_real_bundle()
        task = ScheduledTask(
            id="test_rc_02",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.ROCKETCHAT,
            chat_id="#ops",
        )

        with (
            patch(
                "infrastructure.scheduling.scheduler.executor.build_message",
                return_value="<b>Scheduled</b> report",
            ),
            patch(
                "integrations.rocketchat.scheduled_delivery.resolve_rocketchat_credentials",
                return_value={
                    "server_url": "https://chat.example.com",
                    "auth_token": "tok",
                    "user_id": "u1",
                },
            ),
            patch(
                "integrations.rocketchat.scheduled_delivery.post_rocketchat_message"
            ) as mock_post,
        ):
            mock_post.return_value = (True, "", "msg_rc")
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        args = mock_post.call_args.args
        assert args[0] == "https://chat.example.com"
        assert args[1] == "#ops"
        # HTML tags stripped — Rocket.Chat renders Markdown, not HTML.
        assert args[2] == "Scheduled report"
        assert args[3] == "tok"
        assert args[4] == "u1"

    def test_slack_delivery_fails_with_webhook_when_chat_id_set(self) -> None:
        """Webhook ignores chat_id — must not silently deliver to the wrong channel."""
        _install_real_bundle()
        task = ScheduledTask(
            id="test_sl_webhook_chat",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.SLACK,
            chat_id="C0123ABCD",
        )

        with (
            patch(
                "infrastructure.scheduling.scheduler.executor.build_message",
                return_value="Scheduled report",
            ),
            patch(
                "integrations.slack.scheduled_delivery.resolve_slack_credentials",
                return_value={"webhook_url": "https://hooks.slack.com/services/T/B/x"},
            ),
            patch("integrations.slack.scheduled_delivery.send_slack_webhook_message") as mock_hook,
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is False
        mock_hook.assert_not_called()

    def test_rocketchat_delivery_fails_without_token_credentials(self) -> None:
        _install_real_bundle()
        task = ScheduledTask(
            id="test_rc_03",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.ROCKETCHAT,
            chat_id="#ops",
        )

        with (
            patch(
                "infrastructure.scheduling.scheduler.executor.build_message",
                return_value="Scheduled report",
            ),
            patch(
                "integrations.rocketchat.scheduled_delivery.resolve_rocketchat_credentials",
                return_value={"webhook_url": "https://chat.example.com/hooks/a/b"},
            ),
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        # Webhook-only setups cannot honor the task's explicit chat_id.
        assert result is False

    def test_claim_dedup_prevents_double_execution(self) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.TELEGRAM].result = (True, "", "msg_1")
        task = ScheduledTask(
            id="test_dedup",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            # First execution succeeds
            result1 = execute_task(task, "2026-01-01T09:00", real_runners())
            # Second execution with same fire_time is deduped
            result2 = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result1 is True
        assert result2 is False
        # Only called once due to dedup
        assert len(adapters[Provider.TELEGRAM].calls) == 1

    def test_message_build_failure_records_error(self) -> None:
        task = ScheduledTask(
            id="test_fail",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with patch("infrastructure.scheduling.scheduler.executor.build_message") as mock_build:
            mock_build.side_effect = RuntimeError("Pipeline crashed")
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is False

    @pytest.mark.parametrize(
        ("skill_name", "skill_revision", "error_text"),
        [
            ("missing-skill-xyz", "abc123", "not installed"),
            ("morning-report", "0" * 64, "changed since it was scheduled"),
        ],
    )
    def test_invalid_recurring_skill_is_visible_in_run_history(
        self,
        skill_name: str,
        skill_revision: str,
        error_text: str,
    ) -> None:
        task = ScheduledTask(
            id=f"invalid-{skill_name}",
            kind=TaskKind.RECURRING_SKILL,
            cron="0 8 * * 1-5",
            provider=Provider.INTERACTIVE_SHELL,
            skill_name=skill_name,
            skill_revision=skill_revision,
        )

        result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is False
        runs = get_runs(task.id)
        assert len(runs) == 1
        assert runs[0].status is TaskStatus.FAILED
        assert error_text in runs[0].error

    def test_delivery_failure_records_error(self) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.TELEGRAM].result = (False, "Connection refused", "")
        task = ScheduledTask(
            id="test_del_fail",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100123",
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is False
        assert len(adapters[Provider.TELEGRAM].calls) == 1

    def test_delivery_targets_fan_out_same_message(self) -> None:
        adapters = _install_fake_bundle()
        adapters[Provider.SLACK].result = (True, "", "ts_123")
        adapters[Provider.TELEGRAM].result = (True, "", "msg_42")
        task = ScheduledTask(
            id="test_fanout",
            kind=TaskKind.WORK_ITEM_CHECKIN,
            cron="0 9 * * *",
            provider=Provider.SLACK,
            chat_id="C123",
            params={
                "delivery_targets": json.dumps(
                    [
                        {"provider": "slack", "chat_id": "C123"},
                        {"provider": "telegram", "chat_id": "-100123"},
                    ]
                )
            },
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        slack_call = adapters[Provider.SLACK].calls[-1]
        telegram_call = adapters[Provider.TELEGRAM].calls[-1]
        assert slack_call[0].chat_id == "C123"
        assert telegram_call[0].chat_id == "-100123"
        assert slack_call[1] == "Scheduled report"
        assert telegram_call[1] == "Scheduled report"

    def test_delivery_targets_partial_success_completes_claim(self) -> None:
        """Both fan-out paths agree: posted messages are not thrown away.

        The delivery-targets path used to fail the whole run after Slack had
        already been posted to, so run history claimed nothing was delivered.
        """
        from infrastructure.scheduling.scheduler.storage.run_store import get_runs

        adapters = _install_fake_bundle()
        adapters[Provider.SLACK].result = (True, "", "ts_123")
        adapters[Provider.TELEGRAM].result = (False, "missing token", "")
        task = ScheduledTask(
            id="test_fanout_fail",
            kind=TaskKind.WORK_ITEM_CHECKIN,
            cron="0 9 * * *",
            provider=Provider.SLACK,
            chat_id="C123",
            params={
                "delivery_targets": json.dumps(
                    [
                        {"provider": "slack", "chat_id": "C123"},
                        {"provider": "telegram", "chat_id": "-100123"},
                    ]
                )
            },
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        runs = get_runs(task.id)
        assert runs[0].status.value == "success"
        assert "slack:C123:ts_123" in runs[0].posted_message_id
        assert "partial delivery" in runs[0].error
        # The healthy destination is posted to once; only the failure retries.
        assert len(adapters[Provider.SLACK].calls) == 1
        assert len(adapters[Provider.TELEGRAM].calls) == 3

    def test_empty_message_skips_delivery(self) -> None:
        adapters = _install_fake_bundle()
        task = ScheduledTask(
            id="test_quiet_uptime",
            kind=TaskKind.SENTRY_UPTIME_WATCH,
            cron="*/5 * * * *",
            provider=Provider.SLACK,
            chat_id="C123",
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        assert adapters[Provider.SLACK].calls == []


class _BlockingAdapter:
    """Blocks on a barrier before returning, proving deliveries overlap.

    Serial fan-out can never gather ``parties`` threads at the barrier, so the
    wait times out and the delivery reports the broken barrier as its error —
    the assertion fails on the outcome rather than hanging the suite.
    """

    def __init__(self, barrier: threading.Barrier, result: tuple[bool, str, str]) -> None:
        self.barrier = barrier
        self.result = result
        self.calls: list[tuple[ScheduledTask, str]] = []

    def deliver(self, task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        self.calls.append((task, message))
        try:
            self.barrier.wait()
        except threading.BrokenBarrierError:
            return False, "delivery did not overlap", ""
        return self.result


class _OrderedAdapter:
    """Finishes only after ``after`` is set, then sets ``done``.

    Serial fan-out never sets ``after`` while this delivery is waiting, so the
    wait times out and the delivery reports a failure the test asserts against.
    """

    def __init__(self, after: threading.Event | None, done: threading.Event) -> None:
        self.after = after
        self.done = done

    def deliver(self, _task: ScheduledTask, _message: str) -> tuple[bool, str, str]:
        if self.after is not None and not self.after.wait(timeout=_SYNC_TIMEOUT_SECONDS):
            return False, "delivery did not overlap", ""
        self.done.set()
        return True, "", "msg"


class _FlakyAdapter:
    """Fails its first ``failures`` calls, then succeeds."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def deliver(self, _task: ScheduledTask, _message: str) -> tuple[bool, str, str]:
        self.calls += 1
        if self.calls <= self.failures:
            return False, "rate limited", ""
        return True, "", f"msg_{self.calls}"


def _install_bundle(adapters: dict[Provider, Any]) -> None:
    delivery_bundle.ScheduledDeliveryAdapters(adapters).install()


def _fanout_task(task_id: str, channels: str) -> ScheduledTask:
    return ScheduledTask(
        id=task_id,
        kind=TaskKind.MANUAL_LOOP,
        cron="0 9 * * *",
        provider=Provider.INTERACTIVE_SHELL,
        params={LOOP_CHANNELS_PARAM: channels},
    )


@pytest.mark.usefixtures("_tmp_stores")
class TestDeliveryFanOutConcurrency:
    """Fan-out overlaps destinations and reports them in a stable order."""

    def test_destinations_are_delivered_to_concurrently(self) -> None:
        from infrastructure.scheduling.scheduler.storage.run_store import get_runs

        barrier = threading.Barrier(3, timeout=_SYNC_TIMEOUT_SECONDS)
        adapters: dict[Provider, Any] = {
            provider: _BlockingAdapter(barrier, (True, "", f"{provider.value}_id"))
            for provider in (Provider.INTERACTIVE_SHELL, Provider.SLACK, Provider.TELEGRAM)
        }
        _install_bundle(adapters)
        task = _fanout_task("test_overlap", "interactive_shell,slack,telegram")

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        runs = get_runs(task.id)
        assert [outcome.ok for outcome in runs[0].targets] == [True, True, True]
        assert runs[0].error == ""

    def test_target_outcomes_persist_in_plan_order_not_completion_order(self) -> None:
        """The last destination to finish is still reported first."""
        from infrastructure.scheduling.scheduler.storage.run_store import get_runs

        slack_done = threading.Event()
        shell_done = threading.Event()
        # interactive_shell is planned first but waits for slack to finish.
        adapters: dict[Provider, Any] = {
            Provider.INTERACTIVE_SHELL: _OrderedAdapter(slack_done, shell_done),
            Provider.SLACK: _OrderedAdapter(None, slack_done),
        }
        _install_bundle(adapters)
        task = _fanout_task("test_order", "interactive_shell,slack")

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        assert shell_done.is_set()
        runs = get_runs(task.id)
        assert [outcome.ok for outcome in runs[0].targets] == [True, True]
        assert [outcome.provider for outcome in runs[0].targets] == [
            Provider.INTERACTIVE_SHELL,
            Provider.SLACK,
        ]

    def test_retry_targets_only_the_failed_destination(self) -> None:
        from infrastructure.scheduling.scheduler.storage.run_store import get_runs

        flaky = _FlakyAdapter(failures=2)
        healthy = _FakeAdapter()
        healthy.result = (True, "", "local:1")
        _install_bundle({Provider.SLACK: flaky, Provider.INTERACTIVE_SHELL: healthy})
        task = _fanout_task("test_retry_scope", "interactive_shell,slack")

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is True
        assert flaky.calls == 3
        assert len(healthy.calls) == 1
        runs = get_runs(task.id)
        assert runs[0].error == ""
        assert [outcome.attempts for outcome in runs[0].targets] == [1, 3]

    def test_all_destinations_failing_fails_the_run(self) -> None:
        from infrastructure.scheduling.scheduler.storage.run_store import get_runs

        adapters = _install_fake_bundle()
        adapters[Provider.INTERACTIVE_SHELL].result = (False, "inbox unwritable", "")
        adapters[Provider.SLACK].result = (False, "webhook missing", "")
        task = _fanout_task("test_all_fail", "interactive_shell,slack")

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is False
        runs = get_runs(task.id)
        assert runs[0].status.value == "failed"
        assert "partial delivery" not in runs[0].error
        assert [outcome.ok for outcome in runs[0].targets] == [False, False]

    def test_unsupported_loop_channel_records_the_parse_error(self) -> None:
        from infrastructure.scheduling.scheduler.storage.run_store import get_runs

        _install_fake_bundle()
        task = _fanout_task("test_bad_channel", "interactive_shell,carrier_pigeon")

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(task, "2026-01-01T09:00", real_runners())

        assert result is False
        runs = get_runs(task.id)
        assert "carrier_pigeon" in runs[0].error
        assert runs[0].targets == ()


@pytest.mark.usefixtures("_tmp_stores")
class TestSelectiveRerun:
    """execute_task's target_filter -- what --failed-only ultimately drives."""

    def test_target_filter_delivers_only_to_the_named_destinations(self) -> None:
        from infrastructure.scheduling.scheduler.types import Provider as P

        adapters = _install_fake_bundle()
        adapters[P.SLACK].result = (True, "", "ts_retry")
        task = ScheduledTask(
            id="test_rerun_filtered",
            kind=TaskKind.WORK_ITEM_CHECKIN,
            cron="0 9 * * *",
            provider=P.SLACK,
            chat_id="C123",
            params={
                "delivery_targets": json.dumps(
                    [
                        {"provider": "slack", "chat_id": "C123"},
                        {"provider": "telegram", "chat_id": "-100123"},
                    ]
                )
            },
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(
                task,
                "2026-01-01T09:00",
                real_runners(),
                target_filter=frozenset({(P.SLACK, "C123")}),
            )

        assert result is True
        # Only the named destination is contacted -- the previously-succeeded
        # Telegram destination is never touched by the rerun.
        assert len(adapters[P.SLACK].calls) == 1
        assert len(adapters[P.TELEGRAM].calls) == 0

    def test_an_empty_target_filter_delivers_to_nobody(self) -> None:
        adapters = _install_fake_bundle()
        task = ScheduledTask(
            id="test_rerun_nothing_to_do",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )

        with patch(
            "infrastructure.scheduling.scheduler.executor.build_message",
            return_value="Scheduled report",
        ):
            result = execute_task(
                task, "2026-01-01T09:00", real_runners(), target_filter=frozenset()
            )

        assert result is False
        assert adapters[Provider.TELEGRAM].calls == []
