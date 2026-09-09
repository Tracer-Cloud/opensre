"""SessionGoal completion — judge decides met; tools are required to accept.

The action model does not get to close the goal by saying it is done. This
module merges tool ticks, validates newly ticked items, then asks the
transcript judge (:mod:`core.agent_harness.session_goal.judge`).
``GOAL_REACHED`` needs tool or stored-finding evidence and a quote from
those observations when tools ran. ``NOT_REACHED``
keeps the goal active so the next turn continues — successful tools are
not enough. The judge may also veto a ``Contradiction:`` or declare
``IMPOSSIBLE``. An unrecovered tool error this turn blocks a reached
verdict. Any this-turn tool error blocks host accept when the judge is
missing. Overflowed tool evidence (``tool_evidence is None``) stays
unverified, including after ``GOAL_REACHED``. Reply prose never ticks
an item.

The judge client is injected: hosts build the loop's evaluate with
:func:`build_session_goal_evaluator`. A missing or broken judge does not
block host accept after real tools.
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
    SessionGoalReading,
    invoke_session_goal_judge,
    judge_reason_is_contradiction,
    read_observations,
)
from core.agent_harness.session_goal.plan_credit import credit_completed_plan_steps
from core.agent_harness.session_goal.progress import is_session_goal_progress_text
from core.agent_harness.session_goal.review_input import (
    tool_evidence_has_failure,
    tool_evidence_has_unrecovered_failure,
)
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
    #: The judge said this verdict repeats the previous turn's blocking problem.
    repeats_previous: bool = False


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
    qualified = getattr(action, "evidence_success_count", None)
    if qualified is not None:
        return bool(qualified > 0)
    action_succeeded = 0
    if action is not None:
        try:
            action_succeeded = int(getattr(action, "executed_success_count", 0) or 0)
        except (TypeError, ValueError):
            action_succeeded = 0
    return action_succeeded - max(0, bookkeeping_calls) > 0


def goal_has_session_goal_evidence(goal: SessionGoal, result: Any) -> bool:
    """True when this turn succeeded at a tool, or an earlier turn stored findings."""
    return turn_has_session_goal_evidence(result) or goal.tool_success_seen or bool(goal.findings)


def _need_tool_evidence_reason(judge_reason: str) -> str:
    extra = judge_reason.strip()
    if extra:
        return f"{SessionGoalReason.NEED_TOOL_EVIDENCE} — {extra}"
    return SessionGoalReason.NEED_TOOL_EVIDENCE


def _host_can_accept(
    *,
    evidence: bool,
    unfinished: bool,
    tool_failed: bool,
    unverified: bool,
) -> bool:
    """Host accept: real evidence, no open item, no failed tool, evidence still reviewable."""
    return bool(evidence) and not unfinished and not tool_failed and not unverified


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
    tool_evidence: str,
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
            tool_evidence=tool_evidence,
            findings=current.findings,
            prior_tool_evidence=current.tool_evidence,
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
    tool_evidence: str,
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
                previous_reason=current.last_verdict,
            )
        if judge_llm is None:
            return None
        reading = read_observations(
            judge_llm,
            condition=current.condition,
            tool_evidence=tool_evidence,
            prior_tool_evidence=current.tool_evidence,
        )
        parsed = invoke_session_goal_judge(
            judge_llm,
            condition=current.condition,
            reply=text,
            evidence=evidence,
            unfinished=unfinished,
            tool_evidence=tool_evidence,
            findings=current.findings,
            prior_tool_evidence=current.tool_evidence,
            previous_reason=current.last_verdict,
            independent_reading=reading.answer if reading is not None else "",
        )
        return _accept_agreeing_reading(parsed, reading)
    except Exception:
        log.debug("session-goal judge unavailable", exc_info=True)
        return None


def _normalize_quote(text: str) -> str:
    return " ".join(text.split()).casefold()


def judge_quote_is_supported(quote: str, observations: str) -> bool:
    """True when the judge's quote appears in what it was shown (whitespace-insensitive)."""
    needle = _normalize_quote(quote)
    return bool(needle) and needle in _normalize_quote(observations)


def _blocking_verdict_unsupported(
    parsed: SessionGoalJudgeVerdict, *, tool_evidence: str, reply: str
) -> bool:
    """A blocking verdict over tool observations must quote them or the reply.

    Without tool observations the judge reasons from the reply and the
    condition alone, so an impossible verdict needs no quote there.
    """
    reason = parsed.reason.strip()
    contradiction = judge_reason_is_contradiction(reason)
    impossible = parsed.verdict == "IMPOSSIBLE" and bool(tool_evidence.strip())
    if not (contradiction or impossible):
        return False
    quote = getattr(parsed, "evidence_quote", "")
    return not judge_quote_is_supported(quote, f"{tool_evidence}\n{reply}")


def _accept_agreeing_reading(
    parsed: SessionGoalJudgeVerdict | None, reading: SessionGoalReading | None
) -> SessionGoalJudgeVerdict | None:
    """Turn a not-yet into reached when two independent views agree the work is done.

    The reading (made without the reply) says the observations cover every
    item; the judge says the reply matches that reading and names no
    contradiction. A judge that still asks for more at that point is asking
    for evidence of an event that did not happen.
    """
    if parsed is None or reading is None:
        return parsed
    if parsed.verdict != "NOT_REACHED" or not reading.covered:
        return parsed
    if not parsed.reply_matches_reading or judge_reason_is_contradiction(parsed.reason):
        return parsed
    return parsed.model_copy(
        update={"verdict": "GOAL_REACHED", "reason": SessionGoalReason.AGREES_WITH_READING}
    )


def _reached_verdict_unsupported(parsed: SessionGoalJudgeVerdict, *, tool_evidence: str) -> bool:
    """``GOAL_REACHED`` after tools must quote the observations, not the reply.

    The assistant table can say Yes while ``gh`` only shows attempt 1. A
    quote taken from that table is not checkable against the world. A verdict
    promoted because the reply agrees with the independent reading carries
    that reading as its support instead of a quote.
    """
    if parsed.verdict != "GOAL_REACHED" or not tool_evidence.strip():
        return False
    if parsed.reason == SessionGoalReason.AGREES_WITH_READING:
        return False
    quote = getattr(parsed, "evidence_quote", "")
    return not judge_quote_is_supported(quote, tool_evidence)


def _verdict_from_judge(
    parsed: SessionGoalJudgeVerdict | None,
    *,
    evidence: bool,
    host_can_accept: bool,
    judge_can_accept: bool,
    tool_failed: bool,
    unverified: bool,
    fallback_reason: str,
    tool_evidence: str = "",
    reply: str = "",
) -> SessionGoalVerdict:
    if parsed is None:
        if host_can_accept:
            return SessionGoalVerdict(
                status=SessionGoalStatus.ACHIEVED,
                reason=SessionGoalReason.ACHIEVED_TOOL_EVIDENCE,
            )
        return SessionGoalVerdict(
            status=SessionGoalStatus.ACTIVE,
            reason=SessionGoalReason.JUDGE_UNAVAILABLE,
        )
    reason = parsed.reason.strip()
    repeated = bool(getattr(parsed, "repeats_previous", False))
    if _blocking_verdict_unsupported(parsed, tool_evidence=tool_evidence, reply=reply):
        # The judge blocked without pointing at the data: keep working, do not
        # end the goal or pause on a claim nobody can check.
        return SessionGoalVerdict(
            status=SessionGoalStatus.ACTIVE,
            reason=SessionGoalReason.judge_unsupported(reason or fallback_reason),
        )
    if parsed.verdict == "IMPOSSIBLE":
        return SessionGoalVerdict(
            status=SessionGoalStatus.IMPOSSIBLE,
            reason=reason or SessionGoalReason.IMPOSSIBLE,
        )
    if judge_reason_is_contradiction(reason):
        return SessionGoalVerdict(
            status=SessionGoalStatus.ACTIVE,
            reason=reason,
            repeats_previous=repeated,
        )
    if parsed.verdict == "GOAL_REACHED":
        if _reached_verdict_unsupported(parsed, tool_evidence=tool_evidence):
            return SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason=SessionGoalReason.judge_unsupported(reason or fallback_reason),
                repeats_previous=repeated,
            )
        # Same overflow / unfinished / failed-tool gate as the host. A cheap
        # GOAL_REACHED must not close on unreviewable or incomplete work.
        if judge_can_accept:
            return SessionGoalVerdict(
                status=SessionGoalStatus.ACHIEVED,
                reason=reason or SessionGoalReason.ACHIEVED_TOOL_EVIDENCE,
            )
        if unverified:
            return SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason=SessionGoalReason.UNVERIFIED_OVERFLOW,
                repeats_previous=repeated,
            )
        if tool_failed:
            return SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason=reason or fallback_reason,
                repeats_previous=repeated,
            )
        if not evidence:
            return SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason=_need_tool_evidence_reason(reason),
                repeats_previous=repeated,
            )
        return SessionGoalVerdict(
            status=SessionGoalStatus.ACTIVE,
            reason=reason or fallback_reason,
            repeats_previous=repeated,
        )
    return SessionGoalVerdict(
        status=SessionGoalStatus.ACTIVE,
        reason=reason or fallback_reason,
        repeats_previous=repeated,
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
    evidence = turn_evidence or current.tool_success_seen or bool(current.findings)
    tool_evidence = getattr(getattr(result, "action_result", None), "tool_evidence", "")
    if turn_evidence:
        current = current.with_tool_progress()

    newly = current.new_ticks | (current.completed - completed_before)
    review = _review_ticks(
        current,
        newly=newly,
        text=text,
        evidence=evidence,
        tool_evidence=tool_evidence,
        validate=validate,
        validate_llm=validate_llm,
    )
    kept = review.kept or frozenset()
    if kept != newly:
        current = current.with_completed((current.completed - newly) | kept)
    if current.new_ticks or current.bookkeeping_calls:
        current = replace(current, new_ticks=frozenset(), bookkeeping_calls=0)

    any_failure = tool_evidence_has_failure(tool_evidence)
    unrecovered = tool_evidence_has_unrecovered_failure(tool_evidence)
    unverified = current.tool_evidence is None
    unfinished = bool(current.unfinished_items)
    host_can_accept = _host_can_accept(
        evidence=evidence,
        unfinished=unfinished,
        tool_failed=any_failure,
        unverified=unverified,
    )
    # The judge may complete an open checklist (``_complete_checklist``).
    # Overflow and an unrecovered write still block.
    judge_can_accept = _host_can_accept(
        evidence=evidence,
        unfinished=False,
        tool_failed=unrecovered,
        unverified=unverified,
    )
    if current.checklist_complete and evidence and judge is None and judge_llm is None:
        if host_can_accept:
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACHIEVED,
                reason=SessionGoalReason.CHECKLIST_COMPLETE,
            )
        elif any_failure:
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason=SessionGoalReason.TOOL_FAILED,
            )
        else:
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason=SessionGoalReason.UNVERIFIED_OVERFLOW,
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
            tool_evidence=tool_evidence,
            judge=judge,
            judge_llm=judge_llm,
        )
        verdict = _verdict_from_judge(
            parsed,
            evidence=evidence,
            host_can_accept=host_can_accept,
            judge_can_accept=judge_can_accept,
            tool_failed=unrecovered,
            unverified=unverified,
            fallback_reason=derive_session_goal_reason(current),
            tool_evidence=tool_evidence,
            reply=text,
        )
    verdict = _with_rejected_ticks(verdict, review.rejected)
    if verdict.status == SessionGoalStatus.ACHIEVED:
        current = _complete_checklist(current)
    # A first verdict cannot repeat a previous one. Cheap judges still set the
    # flag when last_verdict is empty, which stalled live /goal after one turn.
    repeated = verdict.repeats_previous and bool(current.last_verdict.strip())
    current = current.with_verdict(verdict.reason, repeated=repeated)

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
        if llm is None:
            return default_evaluate_session_goal(
                goal, result, session=session, judge=lambda **_kw: None, validate=lambda **_kw: None
            )
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
    "judge_quote_is_supported",
    "session_goal_reply_text",
    "turn_has_session_goal_evidence",
]
