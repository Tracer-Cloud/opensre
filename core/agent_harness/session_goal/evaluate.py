"""SessionGoal completion — cheap-model judge plus a tool-evidence gate.

The action model does not get to close the goal by saying it is done. This
module merges tool ticks, validates newly ticked items, then asks the
transcript judge (:mod:`core.agent_harness.session_goal.judge`) for met /
not yet / impossible. ``GOAL_REACHED`` without this-turn tools or stored
findings stays active. Prose ``done=`` tags are ignored.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalReason,
    SessionGoalStatus,
    attach_session_goal,
    derive_session_goal_reason,
)
from core.agent_harness.session_goal.judge import (
    SessionGoalJudgeVerdict,
    default_classification_llm,
    invoke_session_goal_judge,
)
from core.agent_harness.session_goal.plan_credit import credit_completed_plan_steps
from core.agent_harness.session_goal.progress import is_session_goal_progress_text
from core.agent_harness.session_goal.validate import (
    invoke_checklist_tick_validator,
    kept_tick_indices,
)
from core.llm.types import AgentLLMClient

log = logging.getLogger(__name__)

JudgeFn = Callable[..., SessionGoalJudgeVerdict | None]
ValidateFn = Callable[..., frozenset[int] | None]


@dataclass(frozen=True, slots=True)
class SessionGoalVerdict:
    """Host decision for one session-goal evaluation."""

    status: str
    reason: str


def session_goal_reply_text(result: Any) -> str:
    """Best assistant reply text from a turn result (evaluate / loop shared)."""
    response = getattr(result, "assistant_response_text", None)
    if isinstance(response, str) and response:
        return response
    primary = getattr(result, "primary_response_text", None)
    if isinstance(primary, str):
        return primary
    return ""


def turn_has_session_goal_evidence(result: Any) -> bool:
    """True when the turn ran a tool **successfully** — not prose, not a claim.

    A tool that ran and errored is not evidence the goal was met, so a failed
    call must not let a ``GOAL_REACHED`` verdict through. ``executed_count``
    alone would say yes to a turn whose only action failed.
    """
    action = getattr(result, "action_result", None)
    action_succeeded = 0
    if action is not None:
        try:
            action_succeeded = int(getattr(action, "executed_success_count", 0) or 0)
        except (TypeError, ValueError):
            action_succeeded = 0
    return action_succeeded > 0


def goal_has_session_goal_evidence(goal: SessionGoal, result: Any) -> bool:
    """True when this turn succeeded at a tool, or an earlier turn stored findings."""
    return turn_has_session_goal_evidence(result) or bool(goal.findings)


def _need_tool_evidence_reason(judge_reason: str) -> str:
    extra = judge_reason.strip()
    if extra:
        return f"{SessionGoalReason.NEED_TOOL_EVIDENCE} — {extra}"
    return SessionGoalReason.NEED_TOOL_EVIDENCE


def _ticked_items(goal: SessionGoal, newly: frozenset[int]) -> tuple[tuple[int, str], ...]:
    return tuple(
        (index, goal.checklist[index])
        for index in sorted(newly)
        if 0 <= index < len(goal.checklist)
    )


def _run_validator(
    current: SessionGoal,
    *,
    newly: frozenset[int],
    text: str,
    evidence: bool,
    validate: ValidateFn | None,
    validate_llm: AgentLLMClient | None,
) -> frozenset[int] | None:
    if not newly:
        return newly
    ticked = _ticked_items(current, newly)
    try:
        if validate is not None:
            return validate(
                newly=newly,
                condition=current.condition,
                reply=text,
                evidence=evidence,
                ticked=ticked,
            )
        llm = validate_llm if validate_llm is not None else default_classification_llm()
        parsed = invoke_checklist_tick_validator(
            llm,
            condition=current.condition,
            reply=text,
            evidence=evidence,
            ticked=ticked,
        )
        if parsed is None:
            return None
        return kept_tick_indices(parsed, newly=newly)
    except Exception:
        log.debug("session-goal tick validator unavailable", exc_info=True)
        return None


def _run_judge(
    current: SessionGoal,
    *,
    text: str,
    evidence: bool,
    judge: JudgeFn | None,
    judge_llm: AgentLLMClient | None,
) -> SessionGoalJudgeVerdict | None:
    unfinished = current.unfinished_items
    try:
        if judge is not None:
            return judge(
                condition=current.condition,
                reply=text,
                evidence=evidence,
                unfinished=unfinished,
            )
        llm = judge_llm if judge_llm is not None else default_classification_llm()
        return invoke_session_goal_judge(
            llm,
            condition=current.condition,
            reply=text,
            evidence=evidence,
            unfinished=unfinished,
        )
    except Exception:
        log.debug("session-goal judge unavailable", exc_info=True)
        return None


def _verdict_from_judge(
    parsed: SessionGoalJudgeVerdict | None,
    *,
    evidence: bool,
    fallback_reason: str,
) -> SessionGoalVerdict:
    if parsed is None:
        return SessionGoalVerdict(
            status=SessionGoalStatus.ACTIVE,
            reason=SessionGoalReason.LLM_CONFIRM_UNAVAILABLE,
        )
    reason = parsed.reason.strip()
    if parsed.verdict == "IMPOSSIBLE":
        return SessionGoalVerdict(
            status=SessionGoalStatus.IMPOSSIBLE,
            reason=reason or SessionGoalReason.IMPOSSIBLE,
        )
    if parsed.verdict == "GOAL_REACHED":
        if evidence:
            return SessionGoalVerdict(
                status=SessionGoalStatus.ACHIEVED,
                reason=reason or SessionGoalReason.ACHIEVED_TOOL_EVIDENCE,
            )
        return SessionGoalVerdict(
            status=SessionGoalStatus.ACTIVE,
            reason=_need_tool_evidence_reason(reason),
        )
    return SessionGoalVerdict(
        status=SessionGoalStatus.ACTIVE,
        reason=reason or fallback_reason,
    )


def evaluate_session_goal(
    goal: SessionGoal,
    result: Any,
    *,
    session: Any | None = None,
    judge: JudgeFn | None = None,
    judge_llm: AgentLLMClient | None = None,
    validate: ValidateFn | None = None,
    validate_llm: AgentLLMClient | None = None,
) -> SessionGoalVerdict:
    """Independent evaluation of a session goal (ticks + judge + evidence gate)."""
    if session is not None and getattr(session, "pending_user_choice", None) is not None:
        return SessionGoalVerdict(
            status=SessionGoalStatus.ACTIVE,
            reason=SessionGoalReason.WAITING_USER_CHOICE,
        )

    text = session_goal_reply_text(result)
    completed_before = goal.completed - goal.new_ticks
    current = goal
    if session is not None:
        stored = getattr(session, "session_goal", None)
        if isinstance(stored, SessionGoal) and stored.completed - current.completed:
            current = current.with_completed(current.completed | stored.completed)
    current = credit_completed_plan_steps(current, session)
    turn_evidence = turn_has_session_goal_evidence(result)
    evidence = turn_evidence or bool(current.findings)
    if turn_evidence:
        current = current.with_tool_progress()

    newly = current.new_ticks | (current.completed - completed_before)
    kept = _run_validator(
        current,
        newly=newly,
        text=text,
        evidence=evidence,
        validate=validate,
        validate_llm=validate_llm,
    )
    ticks_unvalidated = kept is None and bool(newly)
    if kept is not None and kept != newly:
        current = current.with_completed((current.completed - newly) | kept)
    if current.new_ticks:
        current = replace(current, new_ticks=frozenset())

    if current.checklist_complete and evidence and not ticks_unvalidated:
        verdict = SessionGoalVerdict(
            status=SessionGoalStatus.ACHIEVED,
            reason=SessionGoalReason.CHECKLIST_COMPLETE,
        )
    elif is_session_goal_progress_text(text):
        verdict = SessionGoalVerdict(
            status=SessionGoalStatus.ACTIVE,
            reason=current.last_reason.strip() or derive_session_goal_reason(current),
        )
    else:
        parsed = _run_judge(
            current,
            text=text,
            evidence=evidence,
            judge=judge,
            judge_llm=judge_llm,
        )
        verdict = _verdict_from_judge(
            parsed,
            evidence=evidence,
            fallback_reason=derive_session_goal_reason(current),
        )

    if session is not None:
        updated = current.with_status(verdict.status).with_reason(verdict.reason)
        attach_session_goal(session, updated)
    return verdict


def default_evaluate_session_goal(
    goal: SessionGoal,
    result: Any,
    *,
    session: Any | None = None,
    judge: JudgeFn | None = None,
    judge_llm: AgentLLMClient | None = None,
    validate: ValidateFn | None = None,
    validate_llm: AgentLLMClient | None = None,
) -> str:
    """Loop-facing evaluate: status string; reason stored on the session goal."""
    return evaluate_session_goal(
        goal,
        result,
        session=session,
        judge=judge,
        judge_llm=judge_llm,
        validate=validate,
        validate_llm=validate_llm,
    ).status


__all__ = [
    "JudgeFn",
    "SessionGoalVerdict",
    "ValidateFn",
    "default_evaluate_session_goal",
    "evaluate_session_goal",
    "goal_has_session_goal_evidence",
    "session_goal_reply_text",
    "turn_has_session_goal_evidence",
]
