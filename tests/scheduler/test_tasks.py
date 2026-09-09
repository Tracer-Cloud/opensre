"""Tests for per-kind message builders."""

from __future__ import annotations

from pathlib import Path

import pytest

import infrastructure.scheduling.scheduler.tasks as tasks_mod
from infrastructure.scheduling.scheduler.loop_constants import LOOP_PROMPT_PARAM
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from tests.scheduler._bundle import runners_with_agent


class TestMessageBuilders:
    def test_manual_loop_uses_agent_runner(self) -> None:
        task = ScheduledTask(
            id="manual-loop",
            name="Morning ops",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 8 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_PROMPT_PARAM: "Check incidents and summarize risk."},
        )
        captured: dict[str, object] = {}

        def _mock_agent_runner(payload: dict[str, object]) -> str:
            captured.update(payload)
            return "Manual loop report"

        msg = tasks_mod.build_message(task, runners_with_agent(_mock_agent_runner))

        assert msg == "Manual loop report"
        assert captured["source"] == "scheduled_manual_loop"
        assert captured["loop_prompt"] == "Check incidents and summarize risk."
        assert captured["name"] == "Morning ops"

    def test_manual_loop_strips_credentials(self) -> None:
        """Verify credential keys are not forwarded to the agent runner."""
        task = ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
            params={
                LOOP_PROMPT_PARAM: "Check incidents.",
                "bot_token": "secret123",
                "custom_param": "safe_value",
            },
        )
        captured: dict[str, object] = {}

        def _mock_agent_runner(payload: dict[str, object]) -> str:
            captured.update(payload)
            return "report"

        tasks_mod.build_message(task, runners_with_agent(_mock_agent_runner))
        assert "bot_token" not in captured
        assert captured.get("custom_param") == "safe_value"

    def test_manual_loop_failure_raises(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
            params={LOOP_PROMPT_PARAM: "Check incidents."},
        )

        def _raise(_payload: dict[str, object]) -> str:
            raise RuntimeError("LLM unavailable")

        with pytest.raises(RuntimeError, match="Manual loop failed"):
            tasks_mod.build_message(task, runners_with_agent(_raise))

    def test_sentry_morning_digest_uses_agent_runner(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.SENTRY_MORNING_DIGEST,
            cron="0 8 * * *",
            provider=Provider.SLACK,
            chat_id="C123",
            params={"project_slug": "api"},
        )
        captured: dict[str, object] = {}

        def _mock_agent_runner(payload: dict[str, object]) -> str:
            captured.update(payload)
            return "Top clusters: checkout failures"

        msg = tasks_mod.build_message(task, runners_with_agent(_mock_agent_runner))
        assert msg == "Top clusters: checkout failures"
        assert captured["query"] == "is:unresolved"
        assert captured["stats_period"] == "24h"
        assert captured["project_slug"] == "api"

    def test_sentry_morning_digest_failure_raises(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.SENTRY_MORNING_DIGEST,
            cron="0 8 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )

        def _raise(_payload: dict[str, object]) -> str:
            raise RuntimeError("LLM unavailable")

        with pytest.raises(RuntimeError, match="Sentry morning digest failed"):
            tasks_mod.build_message(task, runners_with_agent(_raise))

    def test_sentry_uptime_watch_uses_agent_runner_port(self) -> None:
        task = ScheduledTask(
            id="uptime1",
            kind=TaskKind.SENTRY_UPTIME_WATCH,
            cron="*/5 * * * *",
            provider=Provider.SLACK,
            chat_id="C123",
            params={"project_slug": "api"},
        )
        captured: dict[str, object] = {}

        def _mock_agent_runner(payload: dict[str, object]) -> str:
            captured.update(payload)
            return "CRITICAL downtime: api"

        msg = tasks_mod.build_message(task, runners_with_agent(_mock_agent_runner))
        assert msg == "CRITICAL downtime: api"
        assert captured["source"] == "scheduled_sentry_uptime_watch"
        assert captured["task_id"] == "uptime1"
        assert captured["project_slug"] == "api"

    def test_posthog_metric_report_uses_agent_runner(self) -> None:
        task = ScheduledTask(
            id="ph1",
            kind=TaskKind.POSTHOG_METRIC_REPORT,
            cron="0 8 * * 1",
            provider=Provider.SLACK,
            chat_id="C123",
            params={"stats_period": "30d", "metrics": "dau,signups"},
        )
        captured: dict[str, object] = {}

        def _mock_agent_runner(payload: dict[str, object]) -> str:
            captured.update(payload)
            return "Metric report: DAU up 12%"

        msg = tasks_mod.build_message(task, runners_with_agent(_mock_agent_runner))
        assert msg == "Metric report: DAU up 12%"
        assert captured["source"] == "scheduled_posthog_metric_report"
        assert captured["task_id"] == "ph1"
        assert captured["stats_period"] == "30d"
        assert captured["metrics"] == "dau,signups"

    def test_posthog_metric_report_defaults_period(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.POSTHOG_METRIC_REPORT,
            cron="0 8 * * 1",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )
        captured: dict[str, object] = {}

        def _mock_agent_runner(payload: dict[str, object]) -> str:
            captured.update(payload)
            return "report"

        tasks_mod.build_message(task, runners_with_agent(_mock_agent_runner))
        assert captured["stats_period"] == "7d"

    def test_posthog_metric_report_strips_credentials(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.POSTHOG_METRIC_REPORT,
            cron="0 8 * * 1",
            provider=Provider.SLACK,
            chat_id="C123",
            params={"api_key": "secret", "stats_period": "7d"},
        )
        captured: dict[str, object] = {}

        def _mock_agent_runner(payload: dict[str, object]) -> str:
            captured.update(payload)
            return "report"

        tasks_mod.build_message(task, runners_with_agent(_mock_agent_runner))
        assert "api_key" not in captured
        assert captured["stats_period"] == "7d"

    def test_posthog_metric_report_failure_raises(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.POSTHOG_METRIC_REPORT,
            cron="0 8 * * 1",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )

        def _raise(_payload: dict[str, object]) -> str:
            raise RuntimeError("LLM unavailable")

        with pytest.raises(RuntimeError, match="PostHog metric report failed"):
            tasks_mod.build_message(task, runners_with_agent(_raise))


class TestRecurringSkillBuilders:
    def test_recurring_skill_uses_agent_runner(self) -> None:
        from core.agent_harness.prompts.skills.schedule import find_action_skill, skill_revision

        skill = find_action_skill("delivering-morning-briefings")
        assert skill is not None
        task = ScheduledTask(
            kind=TaskKind.RECURRING_SKILL,
            cron="0 8 * * 1-5",
            provider=Provider.SLACK,
            chat_id="C123",
            skill_name="delivering-morning-briefings",
            skill_revision=skill_revision(skill),
        )
        captured: dict[str, object] = {}

        def _agent(payload: dict[str, object]) -> str:
            captured.update(payload)
            return "Good morning! Weather — Amsterdam: sunny\nTop headlines:\n- One headline"

        msg = tasks_mod.build_message(task, runners_with_agent(_agent))
        assert "Good morning!" in msg
        assert captured["source"] == "scheduled_recurring_skill"
        assert captured["skill_name"] == "delivering-morning-briefings"

    def test_persisted_legacy_skill_name_is_migrated_and_repinned(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A schedule stored before the gerund rename keeps running under the new name."""
        from core.agent_harness.prompts.skills.schedule import find_action_skill, skill_revision
        from infrastructure.scheduling.scheduler.storage.task_store import add_task, list_tasks

        store_path = tmp_path / "tasks.json"
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
            lambda: store_path,
        )
        legacy = add_task(
            ScheduledTask(
                kind=TaskKind.RECURRING_SKILL,
                cron="0 8 * * 1-5",
                provider=Provider.SLACK,
                chat_id="C123",
                skill_name="morning-report",
                skill_revision="0" * 64,
            ),
            store_path,
        )
        captured: dict[str, object] = {}

        def _agent(payload: dict[str, object]) -> str:
            captured.update(payload)
            return "Good morning!"

        assert tasks_mod.build_message(legacy, runners_with_agent(_agent)) == "Good morning!"

        current = find_action_skill("delivering-morning-briefings")
        assert current is not None
        assert captured["skill_name"] == "delivering-morning-briefings"
        (stored,) = list_tasks(store_path)
        assert stored.id == legacy.id
        assert stored.skill_name == "delivering-morning-briefings"
        assert stored.skill_revision == skill_revision(current)

    def test_recurring_skill_revision_mismatch_raises(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.RECURRING_SKILL,
            cron="0 8 * * 1-5",
            provider=Provider.SLACK,
            chat_id="C123",
            skill_name="delivering-morning-briefings",
            skill_revision="0" * 64,
        )
        with pytest.raises(RuntimeError, match="changed since it was scheduled"):
            tasks_mod.build_message(task, runners_with_agent(lambda _p: "ignored"))
