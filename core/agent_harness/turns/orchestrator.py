"""Run one complete chat turn through the shared tool-calling agent."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from core.agent_harness.ports import (
    ConfirmFn,
    ExecuteActions,
    OutputSink,
    SessionState,
    TurnAccounting,
)
from core.agent_harness.prompts.memory.conversation import expand_affirmative_follow_up
from core.agent_harness.session.pending_offer import (
    clear_unconfirmed_pending_offers,
    consume_confirmed_pending_offer,
    first_pending_offer,
    is_pending_offer_confirmation,
)
from core.agent_harness.turns.conversation_recording import record_conversation_turn
from core.agent_harness.turns.host_cancel import host_cancel_requested
from core.agent_harness.turns.transcript_compaction import auto_compact_if_needed
from core.agent_harness.turns.turn_plan import build_turn_plan
from core.agent_harness.turns.turn_results import (
    FINAL_INTENT_CANCELLED,
    ToolCallingTurnResult,
    TurnResult,
)
from core.agent_harness.turns.turn_snapshot import TurnSnapshot

log = logging.getLogger(__name__)

_ITERATION_CAP_MESSAGE = "Agent stopped before producing a final answer (iteration limit reached)."


def stage_turn_error(session: Any, kind: str, message: str) -> None:
    """Best-effort structured error staging for the turn's telemetry flush."""
    terminal = getattr(session, "terminal", None)
    setter = getattr(terminal, "set_pending_turn_error", None)
    if callable(setter):
        setter(kind, message)


def stage_turn_llm_failure(session: Any, *, client: Any | None = None) -> None:
    """Best-effort staging of the attempted agent LLM identity."""
    from core.agent_harness.accounting.token_accounting import (
        LlmRunInfo,
        resolve_model_name,
        resolve_provider_name,
    )

    terminal = getattr(session, "terminal", None)
    setter = getattr(terminal, "set_pending_turn_llm", None)
    if not callable(setter):
        return
    model = resolve_model_name(client) if client is not None else None
    provider = resolve_provider_name(client) if client is not None else None
    if model or provider:
        setter(LlmRunInfo(model=model, provider=provider))


def _cancelled_turn_result(
    accounting: TurnAccounting,
    action_result: ToolCallingTurnResult,
) -> TurnResult:
    cancelled_action = (
        action_result if action_result.cancelled else replace(action_result, cancelled=True)
    )
    return accounting.finalize(
        TurnResult(
            final_intent=FINAL_INTENT_CANCELLED,
            action_result=cancelled_action,
        )
    )


def run_turn(
    text: str,
    session: SessionState,
    *,
    execute_actions: ExecuteActions,
    accounting: TurnAccounting,
    confirm_fn: ConfirmFn | None = None,
    is_tty: bool | None = None,
    surface: str = "interactive_shell",
    output: OutputSink | None = None,
) -> TurnResult:
    """Run one ReAct turn whose accepted conclusion is the user-facing answer."""
    auto_compact_if_needed(session)
    prior_messages = getattr(session, "cli_agent_messages", None) or ()
    expanded = expand_affirmative_follow_up(
        text,
        prior_messages,
        pending_offer=first_pending_offer(session),
    )
    confirms_pending = is_pending_offer_confirmation(session, expanded)
    if not confirms_pending:
        clear_unconfirmed_pending_offers(session)
    text = expanded

    turn_plan = build_turn_plan(
        TurnSnapshot.from_session(text, session, surface=surface),
        session,
    )
    session.last_command_observation = None
    action_result = execute_actions(
        text,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
        turn_plan=turn_plan,
    )
    if confirms_pending and action_result.executed_success_count > 0:
        consume_confirmed_pending_offer(session, expanded)
    accounting.record_action_result(action_result)

    if action_result.cancelled or host_cancel_requested(output):
        log.debug("turn cancelled after agent run")
        return _cancelled_turn_result(accounting, action_result)

    response_text = action_result.response_text.strip()
    if action_result.hit_iteration_cap and not action_result.response_streamed:
        response_text = "\n\n".join(filter(None, (response_text, _ITERATION_CAP_MESSAGE)))
    if response_text:
        record_conversation_turn(session, text, response_text)
    return accounting.finalize(
        TurnResult(
            final_intent=(
                "agent_incomplete" if action_result.hit_iteration_cap else "agent_completed"
            ),
            action_result=action_result,
            assistant_response_text=response_text,
        )
    )


__all__ = ["run_turn", "stage_turn_error", "stage_turn_llm_failure"]
