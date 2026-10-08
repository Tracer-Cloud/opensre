"""Tests for per-kind message builders."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import infrastructure.scheduling.scheduler.tasks as tasks_mod
from config.prompt_log import PromptLogConfig
from core.agent.run_io import AgentRunResult
from core.agent_harness.harness import AgentSession, SessionStartupResult
from core.agent_harness.prompts.loop_templates import load_loop_template
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.turns import action_driver
from infrastructure.analytics import provider as analytics_provider
from infrastructure.analytics.events import Event
from infrastructure.observability.trace.trace_session import (
    TraceSession,
    current_trace_session,
    inherit_trace_session,
)
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_MODE_PARAM,
    LOOP_PROMPT_PARAM,
    LOOP_TEMPLATE_PARAM,
)
from infrastructure.scheduling.scheduler.storage import task_store
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from tests.scheduler._bundle import real_runners, runners_with_agent


class _RecordingAnalytics:
    """Keeps every captured analytics event."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def capture(self, event: str, properties: dict[str, Any] | None = None) -> None:
        self.events.append((str(event), dict(properties or {})))

    def turn_events(self) -> dict[str, dict[str, Any]]:
        """The run and prompt events of the one turn captured, keyed by event name."""
        turn_names = {Event.REACT_TURN_COMPLETED.value, Event.AI_GENERATION.value}
        return {event: props for event, props in self.events if event in turn_names}


class _AnsweringAgent:
    _react_iterations_used = 1
    _react_hit_iteration_cap = False

    def __init__(self) -> None:
        self._react_executed: list[Any] = []

    def run(self, _messages: Any) -> AgentRunResult:
        return AgentRunResult(
            messages=[], final_text="Loop report", executed=[], llm_iterations_used=1
        )


class _LLM:
    _model = "tick-test-model"
    _provider_label = "OpenAI"


def _answering_plan(**kwargs: Any) -> action_driver.ActionTurnPlan:
    return action_driver.ActionTurnPlan(
        agent=_AnsweringAgent(),  # type: ignore[arg-type]
        user_message=kwargs["message"],
        llm=_LLM(),
        max_iterations=8,
    )


class TestTickTraceSession:
    """Every tick's turns are traced under one session, not one per throwaway session."""

    @staticmethod
    def _task() -> ScheduledTask:
        return ScheduledTask(
            id="cf9d8a4169ac",
            name="CI repair: acme/api",
            kind=TaskKind.MANUAL_LOOP,
            cron="*/2 * * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_PROMPT_PARAM: "Fix CI.", LOOP_MODE_PARAM: "agent"},
        )

    @staticmethod
    def _recording_runner(seen: list[TraceSession | None]):  # noqa: ANN205
        def run(_payload: dict[str, object]) -> str:
            seen.append(current_trace_session())
            return "report"

        return run

    def test_shell_hosted_tick_joins_the_shell_session(self) -> None:
        seen: list[TraceSession | None] = []
        runners = runners_with_agent(self._recording_runner(seen)).hosted_by(lambda: "shell-1")

        tasks_mod.build_message(self._task(), runners)

        (bound,) = seen
        assert bound is not None
        assert bound.session_id == "shell-1"
        assert bound.tags == (tasks_mod.SCHEDULED_TRACE_TAG,)
        assert bound.metadata == {
            "task_id": "cf9d8a4169ac",
            "task_name": "CI repair: acme/api",
            "task_kind": "manual_loop",
        }
        assert current_trace_session() is None

    def test_daemon_tick_groups_per_task_and_never_overrides_an_outer_turn(self) -> None:
        seen: list[TraceSession | None] = []
        runners = runners_with_agent(self._recording_runner(seen))

        tasks_mod.build_message(self._task(), runners)
        with inherit_trace_session("outer-turn"):
            tasks_mod.build_message(self._task(), runners)

        daemon, nested = seen
        assert daemon is not None and daemon.session_id == "cf9d8a4169ac"
        # ``/loops run`` inside a turn: same session, still attributed as scheduled work.
        assert nested is not None and nested.session_id == "outer-turn"
        assert nested.tags == (tasks_mod.SCHEDULED_TRACE_TAG,)
        assert nested.metadata["task_id"] == "cf9d8a4169ac"

    def test_turn_analytics_name_the_task_only_inside_its_tick(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        analytics = _RecordingAnalytics()
        monkeypatch.setattr(analytics_provider, "_instance", analytics)
        config = PromptLogConfig(log_path=tmp_path / "prompts.jsonl")
        monkeypatch.setattr(PromptLogConfig, "load", lambda: config)
        monkeypatch.setattr(action_driver, "_build_action_agent", _answering_plan)
        session = SessionCore(store=InMemorySessionStore())
        session.resolved_integrations_cache = {}
        monkeypatch.setattr(
            AgentSession,
            "startup",
            lambda _self: SessionStartupResult(session=session, prompts=None),
        )
        # No store file: the tick's cancel probe reads the task as still scheduled.
        monkeypatch.setattr(task_store, "default_task_store_path", lambda: tmp_path / "tasks.json")

        AgentSession.run_headless_turn("Check incidents.", is_tty=False)
        outside = analytics.turn_events()
        analytics.events.clear()
        tasks_mod.build_message(self._task(), real_runners())
        inside = analytics.turn_events()

        assert set(outside) == set(inside) == {"react_turn_completed", "$ai_generation"}
        assert all("scheduled_task_id" not in props for props in outside.values())
        assert {props["scheduled_task_id"] for props in inside.values()} == {"cf9d8a4169ac"}


class TestMessageBuilders:
    def test_manual_loop_uses_agent_runner(self) -> None:
        task = ScheduledTask(
            id="manual-loop",
            name="Morning ops",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 8 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={
                LOOP_PROMPT_PARAM: "Check incidents and summarize risk.",
                LOOP_MODE_PARAM: "agent",
            },
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
        assert captured[LOOP_MODE_PARAM] == "agent"

    @pytest.mark.parametrize(
        ("template", "expected"),
        [
            ("pr-ci", load_loop_template("pr-ci").prompt),
            ("retired-template", "Stored copy of the old template."),
        ],
    )
    def test_template_loop_runs_the_shipped_text_or_its_stored_copy(
        self, template: str, expected: str
    ) -> None:
        task = ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={
                LOOP_TEMPLATE_PARAM: template,
                LOOP_PROMPT_PARAM: "Stored copy of the old template.",
            },
        )
        captured: dict[str, object] = {}

        def _mock_agent_runner(payload: dict[str, object]) -> str:
            captured.update(payload)
            return "report"

        tasks_mod.build_message(task, runners_with_agent(_mock_agent_runner))

        assert captured["loop_prompt"] == expected

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


def _current_major() -> str:
    from core.agent_harness.prompts.skills.scheduling import find_action_skill

    skill = find_action_skill("delivering-morning-briefings")
    assert skill is not None
    return skill.version.split(".")[0]


class TestRecurringSkillBuilders:
    def test_recurring_skill_uses_agent_runner(self) -> None:
        from core.agent_harness.prompts.skills.scheduling import find_action_skill, skill_revision

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
        from core.agent_harness.prompts.skills.scheduling import find_action_skill, skill_revision
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

    def test_recurring_skill_major_version_change_raises(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.RECURRING_SKILL,
            cron="0 8 * * 1-5",
            provider=Provider.SLACK,
            chat_id="C123",
            skill_name="delivering-morning-briefings",
            skill_revision="v2:99:" + "0" * 64,
        )
        with pytest.raises(RuntimeError, match="changed since it was scheduled"):
            tasks_mod.build_message(task, runners_with_agent(lambda _p: "ignored"))

    def test_recurring_skill_follows_an_edit_and_stores_the_new_pin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An edit within the pinned major version runs and re-pins instead of stopping."""
        from core.agent_harness.prompts.skills.scheduling import find_action_skill, skill_revision
        from infrastructure.scheduling.scheduler.storage.task_store import add_task, list_tasks

        store_path = tmp_path / "tasks.json"
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
            lambda: store_path,
        )
        task = add_task(
            ScheduledTask(
                kind=TaskKind.RECURRING_SKILL,
                cron="0 8 * * 1-5",
                provider=Provider.SLACK,
                chat_id="C123",
                skill_name="delivering-morning-briefings",
                skill_revision=f"v2:{_current_major()}:" + "0" * 64,
            ),
            store_path,
        )

        assert tasks_mod.build_message(task, runners_with_agent(lambda _p: "ran")) == "ran"

        current = find_action_skill("delivering-morning-briefings")
        assert current is not None
        (stored,) = list_tasks(store_path)
        assert stored.skill_revision == skill_revision(current)
