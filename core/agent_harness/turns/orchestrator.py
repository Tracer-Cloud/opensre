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
from infrastructure.analytics.prompt_log.lifecycle import record_prompt_turn
from infrastructure.analytics.prompt_log.recorder import PromptRecorder
from infrastructure.observability.trace.observations import (
    TraceAttributes,
    is_observation_sink_active,
    observe_span,
)
from infrastructure.observability.trace.trace_session import TraceSession, inherit_trace_session
from infrastructure.observability.trace.user_identity import resolve_trace_identity

log = logging.getLogger(__name__)

_ITERATION_CAP_MESSAGE = "Agent stopped before producing a final answer (iteration limit reached)."

#: Root observation of one chat turn; trace input/output derive from it.
_TURN_OBSERVATION_NAME = "handle-turn"


def stage_turn_error(session: Any, kind: str, message: str) -> None:
    """Best-effort structured error staging for the turn's telemetry flush."""
    terminal = getattr(session, "terminal", None)
    setter = getattr(terminal, "set_pending_turn_error", None)
    if callable(setter):
        setter(kind, message)
    recorder = PromptRecorder.current()
    if recorder is not None:
        recorder.set_error(kind, message)


def stage_turn_llm_failure(session: Any, *, client: Any | None = None) -> None:
    """Best-effort staging of the attempted agent LLM identity."""
    from core.agent_harness.accounting.token_accounting import (
        LlmRunInfo,
        resolve_model_name,
        resolve_provider_name,
    )

    terminal = getattr(session, "terminal", None)
    setter = getattr(terminal, "set_pending_turn_llm", None)
    model = resolve_model_name(client) if client is not None else None
    provider = resolve_provider_name(client) if client is not None else None
    if model or provider:
        run = LlmRunInfo(model=model, provider=provider)
        if callable(setter):
            setter(run)
        recorder = PromptRecorder.current()
        if recorder is not None:
            recorder.set_run(run)


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


def _turn_outcome_metadata(result: TurnResult) -> dict[str, Any]:
    action = result.action_result
    return {
        "final_intent": result.final_intent,
        "cancelled": action.cancelled,
        "hit_iteration_cap": action.hit_iteration_cap,
        "executed_count": action.executed_count,
        "executed_success_count": action.executed_success_count,
    }


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
    """Run one ReAct turn whose accepted conclusion is the user-facing answer.

    One turn is one trace for the observation sink: the user text is the trace
    input, the assistant reply the output, ``session_id`` groups the turns of a
    conversation and ``user_id`` names who took the turn. The outermost turn
    owns the session id; a turn nested inside it (a loop run from a command, a
    tool driving a headless turn) inherits it rather than stamping its own.
    """
    with (
        record_prompt_turn(text, session, surface=surface) as recorder,
        inherit_trace_session(getattr(session, "session_id", None)) as trace_session,
        observe_span(
            _TURN_OBSERVATION_NAME,
            input=text,
            trace=_trace_attributes(trace_session, surface),
        ) as observation,
    ):
        result = _run_turn(
            text,
            session,
            execute_actions=execute_actions,
            accounting=accounting,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            surface=surface,
            output=output,
        )
        observation.update(
            output=result.primary_response_text or None,
            metadata=_turn_outcome_metadata(result),
        )
        if recorder is not None:
            if result.cancelled:
                recorder.set_error("cancelled", "Agent execution cancelled.")
            else:
                recorder.set_response(result.primary_response_text)
        return result


def _trace_attributes(trace_session: TraceSession | None, surface: str) -> TraceAttributes:
    """Trace-wide attributes for the root observation; identity is resolved only when exported."""
    tags: tuple[str, ...] = (surface,)
    metadata: dict[str, Any] = {"surface": surface}
    if trace_session is not None:
        tags += trace_session.tags
        metadata.update(trace_session.metadata)
    user_id: str | None = None
    if is_observation_sink_active():
        identity = resolve_trace_identity()
        user_id = identity.user_id
        if identity.installation_id:
            metadata["installation_id"] = identity.installation_id
    return TraceAttributes(
        session_id=trace_session.session_id if trace_session is not None else None,
        user_id=user_id,
        tags=tags,
        metadata=metadata,
    )


def _run_turn(
    text: str,
    session: SessionState,
    *,
    execute_actions: ExecuteActions,
    accounting: TurnAccounting,
    confirm_fn: ConfirmFn | None,
    is_tty: bool | None,
    surface: str,
    output: OutputSink | None,
) -> TurnResult:
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
