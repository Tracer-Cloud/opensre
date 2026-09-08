"""SessionGoal completion — cheap-model judge plus a tool-evidence gate.

The action model does not get to close the goal by saying it is done. This
module merges tool ticks, validates newly ticked items, then asks the
transcript judge (:mod:`core.agent_harness.session_goal.judge`) for met /
not yet / impossible. ``GOAL_REACHED`` without this-turn tools or stored
findings stays active. Reply prose never ticks an item.

The judge client is injected: hosts build the loop's evaluate with
:func:`build_session_goal_evaluator`. Without a judge only a fully ticked
checklist can close a goal.
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
    invoke_session_goal_judge,
)
from core.agent_harness.session_goal.plan_credit import credit_completed_plan_steps
from core.agent_harness.session_goal.progress import is_session_goal_progress_text
from core.agent_harness.session_goal.validate import (
    invoke_checklist_tick_validator,
    kept_tick_indices,
    rejected_tick_reasons,
)
from core.llm.types import AgentLLMClient

log = logging.getLogger(__name__)

JudgeFn = Callable[..., SessionGoalJudgeVerdict | None]
ValidateFn = Callable[..., frozenset[int] | None]
JudgeLlmFactory = Callable[[], AgentLLMClient]


@dataclass(frozen=True, slots=True)
class SessionGoalVerdict:
    """Host decision for one session-goal evaluation."""

    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class _TickReview:
    """Ticks that survived validation, plus why the others were refused."""

    kept: frozenset[int] | None
    rejected: tuple[str, ...]


def session_goal_reply_text(result: Any) -> str:
    """Best assistant reply text from a turn result (evaluate / loop shared)."""
    response = getattr(result, "assistant_response_text", None)
    if isinstance(response, str) and response:
        return response
    primary = getattr(result, "primary_response_text", None)
    if isinstance(primary, str):
        return primary
    return ""


def turn_has_session_goal_evidence(result: Any, *, bookkeeping_calls: int = 0) -> bool:
    """True when the turn ran a tool **successfully** — not prose, not a claim.

    A tool that ran and errored is not evidence the goal was met, so a failed
    call must not let a ``GOAL_REACHED`` verdict through. ``executed_count``
    alone would say yes to a turn whose only action failed. The goal's own
    tools (``session_goal_set``, ``session_goal_complete``) are bookkeeping:
    ``bookkeeping_calls`` of the successes are discounted so a tick cannot be
    the evidence for itself.
    """
    action = getattr(result, "action_result", None)
    action_succeeded = 0
    if action is not None:
        try:
            action_succeeded = int(getattr(action, "executed_success_count", 0) or 0)
        except (TypeError, ValueError):
            action_succeeded = 0
    return action_succeeded - max(0, bookkeeping_calls) > 0


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


def _review_ticks(
    current: SessionGoal,
    *,
    newly: frozenset[int],
    text: str,
    evidence: bool,
    validate: ValidateFn | None,
    validate_llm: AgentLLMClient | None,
) -> _TickReview:
    """Validate this turn's ticks. No validator configured means every tick stands."""
    if not newly:
        return _TickReview(kept=newly, rejected=())
    ticked = _ticked_items(current, newly)
    try:
        if validate is not None:
            kept = validate(
                newly=newly,
                condition=current.condition,
                reply=text,
                evidence=evidence,
                ticked=ticked,
            )
            return _TickReview(kept=kept, rejected=())
        if validate_llm is None:
            return _TickReview(kept=newly, rejected=())
        parsed = invoke_checklist_tick_validator(
            validate_llm,
            condition=current.condition,
            reply=text,
            evidence=evidence,
            ticked=ticked,
        )
    except Exception:
        log.debug("session-goal tick validator unavailable", exc_info=True)
        return _TickReview(kept=None, rejected=())
    if parsed is None:
        return _TickReview(kept=None, rejected=())
    return _TickReview(
        kept=kept_tick_indices(parsed, newly=newly),
        rejected=rejected_tick_reasons(parsed, newly=newly),
    )


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
        if judge_llm is None:
            return None
        return invoke_session_goal_judge(
            judge_llm,
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
            reason=SessionGoalReason.JUDGE_UNAVAILABLE,
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


def _with_rejected_ticks(
    verdict: SessionGoalVerdict, rejected: tuple[str, ...]
) -> SessionGoalVerdict:
    """Tell the user why a tick was refused, on the same status line."""
    if not rejected or verdict.status != SessionGoalStatus.ACTIVE:
        return verdict
    return replace(verdict, reason=f"{verdict.reason} (tick rejected: {rejected[0]})")


def _complete_checklist(goal: SessionGoal) -> SessionGoal:
    """A met goal shows every item ticked, whatever the model remembered to tick."""
    if not goal.checklist or goal.checklist_complete:
        return goal
    return replace(goal, completed=frozenset(range(len(goal.checklist))), new_ticks=frozenset())


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
    bookkeeping = goal.bookkeeping_calls
    if session is not None:
        stored = getattr(session, "session_goal", None)
        if isinstance(stored, SessionGoal):
            # The goal's tools attach onto the session copy; the loop copy may be stale.
            bookkeeping = max(bookkeeping, stored.bookkeeping_calls)
            if stored.completed - current.completed:
                current = current.with_completed(current.completed | stored.completed)
    current = credit_completed_plan_steps(current, session)
    turn_evidence = turn_has_session_goal_evidence(result, bookkeeping_calls=bookkeeping)
    evidence = turn_evidence or bool(current.findings)
    if turn_evidence:
        current = current.with_tool_progress()

    newly = current.new_ticks | (current.completed - completed_before)
    review = _review_ticks(
        current,
        newly=newly,
        text=text,
        evidence=evidence,
        validate=validate,
        validate_llm=validate_llm,
    )
    ticks_unvalidated = review.kept is None and bool(newly)
    if review.kept is not None and review.kept != newly:
        current = current.with_completed((current.completed - newly) | review.kept)
    if current.new_ticks or current.bookkeeping_calls:
        current = replace(current, new_ticks=frozenset(), bookkeeping_calls=0)

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
    verdict = _with_rejected_ticks(verdict, review.rejected)
    if verdict.status == SessionGoalStatus.ACHIEVED:
        current = _complete_checklist(current)

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


def build_session_goal_evaluator(llm_factory: JudgeLlmFactory) -> Callable[..., str]:
    """The loop's evaluate for a host: one cheap-model client judges and validates.

    The client is resolved on the first evaluation, not at build time, so an
    agent that never runs a goal never pays for the client. A factory that
    raises leaves the goal active with :attr:`SessionGoalReason.JUDGE_UNAVAILABLE`.
    """
    client: AgentLLMClient | None = None
    resolved = False

    def _client() -> AgentLLMClient | None:
        nonlocal client, resolved
        if not resolved:
            resolved = True
            try:
                client = llm_factory()
            except Exception:
                log.debug("session-goal judge client unavailable", exc_info=True)
                client = None
        return client

    def _evaluate(goal: SessionGoal, result: Any, *, session: Any | None = None) -> str:
        llm = _client()
        return default_evaluate_session_goal(
            goal, result, session=session, judge_llm=llm, validate_llm=llm
        )

    return _evaluate


__all__ = [
    "JudgeFn",
    "JudgeLlmFactory",
    "SessionGoalVerdict",
    "ValidateFn",
    "build_session_goal_evaluator",
    "default_evaluate_session_goal",
    "evaluate_session_goal",
    "goal_has_session_goal_evidence",
    "session_goal_reply_text",
    "turn_has_session_goal_evidence",
]
