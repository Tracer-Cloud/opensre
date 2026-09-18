"""Telemetry for bounded ReAct agent runs in action and gather phases.

``stop_reason`` mapping from :class:`~core.agent.run_io.AgentRunResult`:

- ``completed`` — loop ended normally (conclusion accepted or tool terminate)
- ``iteration_cap`` — ``hit_iteration_cap`` is true
- ``error`` — ``Agent.run`` raised before returning
- ``cancelled`` — host cancellation during ``Agent.run``
- ``no_tools_needed`` — loop finished without executing any tools
"""

from __future__ import annotations

import sys
import time
from collections.abc import Sequence
from typing import Any, Literal

from core.agent import Agent
from core.agent.run_io import AgentRunResult
from core.agent_harness.ports import SessionState
from core.agent_harness.spi.accounting import (
    LlmRunInfo,
    resolve_model_name,
    resolve_provider_name,
)
from core.messages import RuntimeMessageLike
from infrastructure.analytics.capture import capture_react_turn_completed
from infrastructure.analytics.prompt_log.recorder import PromptRecorder
from infrastructure.analytics.repl_context import (
    get_cli_session_id,
    get_cli_turn_kind,
    get_prompt_turn_id,
)

ReactPhase = Literal["action", "gather"]
ReactStopReason = Literal["completed", "iteration_cap", "error", "cancelled", "no_tools_needed"]


def resolve_react_stop_reason(
    *,
    hit_iteration_cap: bool,
    tool_calls_executed: int,
    error: BaseException | None = None,
    cancelled: bool = False,
) -> ReactStopReason:
    """Map a finished or failed Agent.run to the public stop-reason enum."""
    if cancelled:
        return "cancelled"
    if error is not None:
        return "error"
    if hit_iteration_cap:
        return "iteration_cap"
    if tool_calls_executed == 0:
        return "no_tools_needed"
    return "completed"


def _resolve_cli_session_id(session: SessionState | None) -> str:
    bound = get_cli_session_id()
    if bound:
        return bound
    session_id = getattr(session, "session_id", None) if session is not None else None
    return session_id if isinstance(session_id, str) and session_id else ""


def _partial_result_from_agent(agent: Agent[Any]) -> AgentRunResult | None:
    """Build a partial run result when Agent.run aborts before finalize."""
    iterations_used = int(getattr(agent, "_react_iterations_used", 0) or 0)
    executed = getattr(agent, "_react_executed", None)
    if not isinstance(executed, list):
        executed = []
    if iterations_used == 0 and not executed:
        return None
    return AgentRunResult(
        messages=[],
        final_text="",
        executed=executed,
        hit_iteration_cap=bool(getattr(agent, "_react_hit_iteration_cap", False)),
        llm_iterations_used=iterations_used,
    )


def emit_react_turn_completed(
    *,
    phase: ReactPhase,
    result: AgentRunResult | None,
    iteration_cap: int,
    duration_ms: int,
    llm: Any,
    session: SessionState | None = None,
    error: BaseException | None = None,
    cancelled: bool = False,
) -> None:
    """Emit one ``react_turn_completed`` lifecycle event for an Agent.run."""
    tool_calls_executed = len(result.executed) if result is not None else 0
    llm_iterations_used = result.llm_iterations_used if result is not None else 0
    hit_iteration_cap = bool(result.hit_iteration_cap) if result is not None else False
    stop_reason = resolve_react_stop_reason(
        hit_iteration_cap=hit_iteration_cap,
        tool_calls_executed=tool_calls_executed,
        error=error,
        cancelled=cancelled,
    )
    hit_iteration_cap = stop_reason == "iteration_cap"

    cli_turn_kind = get_cli_turn_kind() or "agent"

    recorder = PromptRecorder.current()
    if recorder is not None:
        recorder.set_run(
            LlmRunInfo(
                model=resolve_model_name(llm),
                provider=resolve_provider_name(llm),
                input_tokens=result.input_tokens if result is not None else None,
                output_tokens=result.output_tokens if result is not None else None,
            )
        )

    capture_react_turn_completed(
        phase=phase,
        llm_iterations_used=llm_iterations_used,
        llm_iteration_cap=iteration_cap,
        hit_iteration_cap=hit_iteration_cap,
        stop_reason=stop_reason,
        tool_calls_executed=tool_calls_executed,
        duration_ms=duration_ms,
        cli_session_id=_resolve_cli_session_id(session),
        cli_turn_kind=cli_turn_kind,
        llm_provider=resolve_provider_name(llm) or "unknown",
        llm_model=resolve_model_name(llm) or "unknown",
        prompt_turn_id=get_prompt_turn_id(),
    )


def run_react_agent_with_telemetry(
    agent: Agent[Any],
    initial_messages: Sequence[RuntimeMessageLike],
    *,
    phase: ReactPhase,
    iteration_cap: int,
    llm: Any,
    session: SessionState | None = None,
) -> AgentRunResult:
    """Run ``agent.run`` and emit exactly one ``react_turn_completed`` event."""
    started = time.monotonic()
    result: AgentRunResult | None = None
    try:
        result = agent.run(initial_messages)
        return result
    finally:
        error = sys.exception() if result is None else None
        emit_react_turn_completed(
            phase=phase,
            result=result if result is not None else _partial_result_from_agent(agent),
            iteration_cap=iteration_cap,
            duration_ms=int((time.monotonic() - started) * 1000),
            llm=llm,
            session=session,
            error=error,
            cancelled=(error is not None and not isinstance(error, Exception))
            or bool(result is not None and result.cancelled),
        )


__all__ = [
    "ReactPhase",
    "ReactStopReason",
    "emit_react_turn_completed",
    "resolve_react_stop_reason",
    "run_react_agent_with_telemetry",
]
