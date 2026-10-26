"""Tests for Session state."""

from __future__ import annotations

from pathlib import Path

import pytest

import config.constants as const_module
from infrastructure.scheduling.task_registry import TaskRegistry
from infrastructure.scheduling.task_types import TaskKind
from surfaces.interactive_shell.session import (
    Session,
)


class TestSession:
    def test_defaults(self) -> None:
        session = Session()
        assert session.history == []
        assert session.accumulated_context == {}
        assert session.terminal.trust_mode is False
        assert session.task_registry.list_recent() == []
        assert session.terminal.metrics.turn_count == 0
        assert session.terminal.metrics.fallback_count == 0
        assert session.terminal.metrics.ctrl_c_intervention_count == 0
        assert session.terminal.metrics.correction_intervention_count == 0
        assert session.terminal.pending_prompt_default is None

    def test_take_pending_prompt_default_returns_and_clears(self) -> None:
        session = Session()
        session.terminal.pending_prompt_default = "why did it fail?"
        assert session.terminal.pop_pending_prompt_default() == "why did it fail?"
        assert session.terminal.pending_prompt_default is None
        assert session.terminal.pop_pending_prompt_default() == ""

    def test_clear_resets_pending_prompt_default(self) -> None:
        session = Session()
        session.terminal.pending_prompt_default = "why did it fail?"
        session.clear()
        assert session.terminal.pending_prompt_default is None

    def test_queue_auto_command_sets_pending_and_notifies(self) -> None:
        session = Session()
        calls: list[bool] = []
        session.terminal.prompt_refresh_fn = lambda: calls.append(True)
        session.terminal.set_auto_command("/integrations setup sentry")
        assert session.terminal.pending_prompt_default == "/integrations setup sentry"
        assert session.terminal.pending_prompt_autosubmit is True
        assert calls == [True]

    def test_queue_auto_prompt_marks_a_plain_turn_and_auto_command_clears_it(self) -> None:
        session = Session()
        session.terminal.set_auto_prompt("analyze acme/app CI reliability")
        assert session.terminal.pending_prompt_autosubmit is True
        assert session.terminal.pop_pending_plain_turn() is True
        assert session.terminal.pending_prompt_plain_turn is False
        session.terminal.set_auto_prompt("again")
        session.terminal.set_auto_command("/goal set done")
        assert session.terminal.pending_prompt_plain_turn is False

    def test_take_pending_autosubmit_returns_and_clears(self) -> None:
        session = Session()
        session.terminal.pending_prompt_autosubmit = True
        assert session.terminal.pop_pending_autosubmit() is True
        assert session.terminal.pending_prompt_autosubmit is False
        assert session.terminal.pop_pending_autosubmit() is False

    def test_clear_resets_pending_autosubmit(self) -> None:
        session = Session()
        session.terminal.set_auto_command("/integrations setup sentry")
        session.clear()
        assert session.terminal.pending_prompt_autosubmit is False
        assert session.terminal.pending_prompt_default is None

    def test_record_appends_entry(self) -> None:
        session = Session()
        session.record("alert", "cpu high")
        session.record("slash", "/status", ok=True)
        session.record("alert", "bad one", ok=False)
        assert len(session.history) == 3
        assert session.history[-1]["type"] == "alert"
        assert session.history[-1]["ok"] is False

    def test_mark_latest_updates_most_recent_matching_kind(self) -> None:
        session = Session()
        session.record("slash", "/status missing.json")
        session.record("alert", "missing.json", ok=False)

        session.mark_latest(ok=False, kind="slash")

        assert session.history[0]["ok"] is False
        assert session.history[1]["ok"] is False

    def test_clear_preserves_trust_mode(self) -> None:
        session = Session()
        session.terminal.trust_mode = True
        session.accumulated_context["service"] = "api"
        session.record("alert", "something")
        session.agent.messages.append(("user", "hey"))
        session.terminal.metrics.record_intervention("ctrl_c")
        session.terminal.metrics.record_intervention("correction")

        assert session.terminal.history_generation == 0
        session.clear()
        assert session.terminal.history_generation == 1

        assert session.history == []
        assert session.accumulated_context == {}
        assert session.agent.messages == []
        assert session.task_registry.list_recent() == []
        assert session.terminal.metrics.ctrl_c_intervention_count == 0
        assert session.terminal.metrics.correction_intervention_count == 0
        assert session.terminal.trust_mode is True  # preserved intentionally

    def test_clear_keeps_persisted_task_history_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = Session()
        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
        session.task_registry = TaskRegistry.persistent()
        task = session.task_registry.create(TaskKind.CLI_COMMAND, command="opensre health")
        task.mark_running()

        session.clear()

        reloaded = TaskRegistry.persistent()
        loaded = reloaded.get(task.task_id)
        assert loaded is not None
        assert loaded.task_id == task.task_id

    def test_record_terminal_turn_updates_aggregates(self) -> None:
        session = Session()

        first = session.terminal.metrics.record_turn(
            executed_count=2,
            executed_success_count=1,
            fallback_to_llm=True,
        )
        second = session.terminal.metrics.record_turn(
            executed_count=1,
            executed_success_count=1,
            fallback_to_llm=False,
        )

        assert first.turn_index == 1
        assert first.fallback_count == 1
        assert first.action_success_percent == 50.0
        assert first.fallback_rate_percent == 100.0

        assert second.turn_index == 2
        assert second.fallback_count == 1
        assert round(second.action_success_percent, 2) == 66.67
        assert second.fallback_rate_percent == 50.0

    def test_record_intervention_increments_per_kind(self) -> None:
        session = Session()

        session.terminal.metrics.record_intervention("ctrl_c")
        session.terminal.metrics.record_intervention("ctrl_c")
        session.terminal.metrics.record_intervention("correction")

        assert session.terminal.metrics.ctrl_c_intervention_count == 2
        assert session.terminal.metrics.correction_intervention_count == 1

    def test_record_intervention_kinds_are_independent(self) -> None:
        """Incrementing one kind does not touch the other."""
        session = Session()

        session.terminal.metrics.record_intervention("correction")

        assert session.terminal.metrics.ctrl_c_intervention_count == 0
        assert session.terminal.metrics.correction_intervention_count == 1

    def test_fresh_session_starts_with_zero_intervention_counts(self) -> None:
        """A new Session does not inherit any prior session's counters."""
        first = Session()
        first.terminal.metrics.record_intervention("ctrl_c")
        first.terminal.metrics.record_intervention("correction")

        second = Session()

        assert second.terminal.metrics.ctrl_c_intervention_count == 0
        assert second.terminal.metrics.correction_intervention_count == 0
