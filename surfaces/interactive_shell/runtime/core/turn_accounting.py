"""Turn-result data model and the single owner of shell-turn accounting.

This module holds the shell's accounting side effects around the core
"facts only" turn-result models: action-agent analytics, terminal-turn
aggregate telemetry, prompt-recorder enrichment, and the final assistant-intent stamp.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

# The neutral "facts only" turn-result models live in the decoupled agent
# package; this module owns only the shell's accounting side effects over them.
from core.agent_harness import ToolCallingTurnResult, TurnResult
from core.agent_harness.spi.accounting import ToolCallingAccountingStatus
from infrastructure.analytics.capture import capture_terminal_turn_summarized
from infrastructure.analytics.prompt_log.recorder import PromptRecorder
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.telemetry.integration_snapshot import (
    build_turn_integration_snapshot,
)


@dataclass
class ShellTurnAccounting:
    """Single owner of a shell turn's accounting side effects.

    Separates "what happened" (decided by the turn flow) from "how it is
    accounted for": action-agent analytics, terminal-turn aggregate telemetry,
    prompt-recorder enrichment, and the final
    assistant-intent stamp.
    """

    session: Session
    text: str

    def record_action_result(self, action_result: ToolCallingTurnResult) -> None:
        """Emit action-agent analytics and update terminal-turn aggregates."""
        self._record_action_analytics(action_result)
        self._record_terminal_turn(action_result)

    def finalize(self, result: TurnResult) -> TurnResult:
        """Enrich prompt telemetry and stamp the session intent."""
        self._enrich_prompt_recorder()
        if result.assistant_response_text and not self._cli_agent_already_recorded():
            # ActionRenderObserver may already have recorded this turn on the
            # first tool_start. Do not append a duplicate history row.
            self.session.record("cli_agent", self.text)
        self.session.last_assistant_intent = result.final_intent
        return result

    def _cli_agent_already_recorded(self) -> bool:
        history = getattr(self.session, "history", None) or ()
        if not history:
            return False
        last = history[-1]
        return (
            isinstance(last, dict)
            and last.get("type") == "cli_agent"
            and last.get("text") == self.text
        )

    def _record_action_analytics(self, action_result: ToolCallingTurnResult) -> None:
        from infrastructure.analytics.capture import (
            capture_repl_execution_policy_decision,
            capture_terminal_actions_executed,
            capture_terminal_actions_planned,
        )

        if action_result.accounting_status == "not_run":
            capture_terminal_actions_executed(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
            )
            return

        capture_terminal_actions_planned(
            planned_count=action_result.planned_count,
            has_unhandled_clause=action_result.has_unhandled_clause,
        )
        capture_repl_execution_policy_decision(
            {
                "policy_stage": "shell_action_agent",
                "policy_trace": "agent_tool_calls"
                if action_result.planned_count
                else "agent_reply",
                "planned_count": action_result.planned_count,
                "has_unhandled_clause": action_result.has_unhandled_clause,
            }
        )
        capture_terminal_actions_executed(
            planned_count=action_result.planned_count,
            executed_count=action_result.executed_count,
            executed_success_count=action_result.executed_success_count,
        )

    def _record_terminal_turn(self, action_result: ToolCallingTurnResult) -> None:
        fallback_to_llm = not action_result.handled
        snapshot = self.session.terminal.metrics.record_turn(
            executed_count=action_result.executed_count,
            executed_success_count=action_result.executed_success_count,
            fallback_to_llm=fallback_to_llm,
        )
        capture_terminal_turn_summarized(
            planned_count=action_result.planned_count,
            executed_count=action_result.executed_count,
            executed_success_count=action_result.executed_success_count,
            fallback_to_llm=fallback_to_llm,
            session_turn_index=snapshot.turn_index,
            session_fallback_count=snapshot.fallback_count,
            session_action_success_percent=snapshot.action_success_percent,
            session_fallback_rate_percent=snapshot.fallback_rate_percent,
        )

    def _enrich_prompt_recorder(self) -> None:
        # Pending turn LLM/error state is consumed unconditionally so a turn
        # that stages it can never leak it into a later turn's flush.
        pending_run = self.session.terminal.pop_pending_turn_llm()
        pending_error = self.session.terminal.pop_pending_turn_error()
        recorder = PromptRecorder.current()
        if recorder is None:
            return
        if pending_error is not None:
            recorder.set_error(pending_error[0], pending_error[1])
        if pending_run is not None:
            recorder.set_run(pending_run)
        with contextlib.suppress(Exception):
            recorder.set_properties(build_turn_integration_snapshot(self.session))


__all__ = [
    "ShellTurnAccounting",
    "TurnResult",
    "ToolCallingAccountingStatus",
    "ToolCallingTurnResult",
]
