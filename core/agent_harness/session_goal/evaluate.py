"""SessionGoal completion — structured verdict, not model self-report alone.

The action/assistant model may emit ``session_goal:achieved``. That tag is a
claim, not proof. This module is the independent host check:

* Checklist complete (via ``done=`` indices) → achieved.
* ``achieved`` with tool evidence on an incomplete **short** checklist (≤2
  items) → complete the checklist (same-turn query+report) and achieve. A
  longer checklist keeps explicit ``done=`` tracking, so the claim is ignored.
* ``achieved`` with an incomplete checklist and **no** tool evidence → stay
  active (ignore the tag).
* Short checklist (≤2 items), no prior-turn progress, tools succeeded, and a
  non-empty reply → achieve even when the model only tagged part of the
  checklist (e.g. ``done=0`` for query, forgot report) or omitted tags
  entirely — avoids a redundant session-goal turn that repeats the answer.
* ``achieved`` on a **host-owned** (``/goal set``) goal → achieved without tools
  (explicit slash-path product rule).
* Host-owned goal, **no** ``achieved`` tag, but tools succeeded (action **or**
  gather) and the reply is non-empty → achieve (same-turn answer). Waiting for
  a scrubbed/forgotten tag forced a redundant outer turn that repeated the
  live answer. Gather successes count: metric handoffs often leave action
  ``executed_success_count`` at 0. Final-route identity alone
  (``cli_agent_fallback`` / summarize) is not evidence — unsupported fallbacks
  must not close the goal.
* Host-owned goal whose reply reports cohort identity unverified (product
  refuse + draft path) → achieve without requiring gather successes.
  Models often stop before a count query; staying ACTIVE forced a redundant
  outer turn.
* ``achieved`` with no checklist on a handoff goal → require tool evidence, or
  stay active.
* Hosts may wrap :func:`evaluate_session_goal` with an LLM confirm for the
  tool-evidence path (:mod:`core.agent_harness.session_goal.confirm`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalReason,
    SessionGoalStatus,
    apply_session_goal_progress,
    attach_session_goal,
)
from core.agent_harness.session_goal.plan_credit import credit_completed_plan_steps
from core.agent_harness.session_goal.progress import is_session_goal_progress_text
from core.agent_harness.turns.cohort_identity import (
    goal_needs_cohort_identity,
    reply_reports_cohort_unverified,
)

# Standalone progress tag — same token shape as strip_session_goal_progress_tags.
_ACHIEVED_CLAIM = re.compile(r"session_goal:achieved")

# Pre-fix host reasons embedded the tag grammar; neutralize before scanning so
# old progress status text cannot look like a claim.
_LEGACY_WAITING_WITH_TAG = (
    "waiting for session_goal:achieved with tool evidence",
    "waiting for session_goal:achieved",
)


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


def reply_claims_session_goal_achieved(text: str) -> bool:
    """True when ``text`` contains a real ``session_goal:achieved`` progress tag.

    Host status reasons never embed tag grammar (:class:`SessionGoalReason`).
    Legacy progress phrases that did are stripped before the token scan.
    """
    if not text:
        return False
    scrubbed = text
    for phrase in _LEGACY_WAITING_WITH_TAG:
        scrubbed = scrubbed.replace(phrase, "")
    return _ACHIEVED_CLAIM.search(scrubbed) is not None


def turn_has_session_goal_evidence(result: Any) -> bool:
    """True when the turn ran a tool **successfully** — not prose, not a claim.

    A tool that ran and errored is not evidence the goal was met, so a failed
    call must not let an ``achieved`` claim through. ``executed_count`` alone
    would say yes to a turn whose only action failed.
    """
    action = getattr(result, "action_result", None)
    action_succeeded = 0
    if action is not None:
        try:
            action_succeeded = int(getattr(action, "executed_success_count", 0) or 0)
        except (TypeError, ValueError):
            action_succeeded = 0
    return action_succeeded > 0


# metric_read-style attach usually emits query + report (2 items). Longer
# walkthroughs must keep explicit ``done=`` tracking so a partial first turn
# cannot false-complete the whole checklist.
_SAME_TURN_CHECKLIST_MAX_ITEMS = 2


def _complete_checklist(goal: SessionGoal) -> SessionGoal:
    return goal.with_completed(frozenset(range(len(goal.checklist))))


def _same_turn_completable(goal: SessionGoal) -> bool:
    """True when one turn may close the whole checklist without ``done=`` tags.

    Applies to both same-turn paths (claimed and unclaimed): a walkthrough long
    enough to span turns must track progress explicitly, so a first turn that
    touched one item cannot mark the rest done.
    """
    return len(goal.checklist) <= _SAME_TURN_CHECKLIST_MAX_ITEMS


def _reply_is_nonempty_and_not_progress_text(text: str) -> bool:
    """True when the assistant reply is real content, not ``/goal`` status chrome."""
    return bool(text.strip()) and not is_session_goal_progress_text(text)


def _short_checklist_has_achieved_claim_and_tool_evidence(
    goal: SessionGoal,
    *,
    claimed: bool,
    has_evidence: bool,
) -> bool:
    """True when a short checklist turn claimed achieved and tools succeeded."""
    return _same_turn_completable(goal) and claimed and has_evidence


def _short_checklist_has_no_prior_progress_and_tool_answer(
    goal: SessionGoal,
    *,
    has_evidence: bool,
    text: str,
    completed_before: frozenset[int],
) -> bool:
    """True when the first short-checklist turn already has tools and a reply."""
    return (
        _same_turn_completable(goal)
        and has_evidence
        and bool(text.strip())
        and not completed_before
    )


def _host_owned_achieved_claim_lacks_tool_evidence(
    goal: SessionGoal,
    *,
    has_evidence: bool,
) -> bool:
    """True when a host-owned ``/goal`` achieved-claim has no tool evidence."""
    return goal.host_owned and not has_evidence


def _host_owned_goal_has_unverified_cohort_reply(goal: SessionGoal, text: str) -> bool:
    """True when a host-owned signup/retention ``/goal`` reply says identity is open.

    Prefer this over tool-evidence achieve so optional LLM confirm cannot veto
    a correct refuse+draft as "metric not reached".
    """
    return (
        goal.host_owned
        and _reply_is_nonempty_and_not_progress_text(text)
        and goal_needs_cohort_identity(goal.condition)
        and reply_reports_cohort_unverified(text)
    )


def _host_owned_goal_has_tool_evidence_and_answer_reply(
    goal: SessionGoal,
    text: str,
    *,
    has_evidence: bool,
) -> bool:
    """True when a host-owned ``/goal`` already has tools and a real answer reply.

    Do not wait for ``session_goal:achieved`` — that tag is scrubbed from the
    visible reply and models often omit it.
    """
    return goal.host_owned and has_evidence and _reply_is_nonempty_and_not_progress_text(text)


def evaluate_session_goal(
    goal: SessionGoal,
    result: Any,
    *,
    session: Any | None = None,
) -> SessionGoalVerdict:
    """Independent structured evaluation of an session goal."""
    if session is not None and getattr(session, "pending_user_choice", None) is not None:
        return SessionGoalVerdict(
            status=SessionGoalStatus.ACTIVE,
            reason=SessionGoalReason.WAITING_USER_CHOICE,
        )

    text = session_goal_reply_text(result)
    current = goal
    if session is not None:
        stored = getattr(session, "session_goal", None)
        if isinstance(stored, SessionGoal):
            current = stored
    completed_before = current.completed
    current = apply_session_goal_progress(current, text)
    current = credit_completed_plan_steps(current, session)

    claimed = reply_claims_session_goal_achieved(text)
    evidence = turn_has_session_goal_evidence(result)

    if current.checklist:
        if current.checklist_complete:
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACHIEVED,
                reason=SessionGoalReason.CHECKLIST_COMPLETE,
            )
        elif _short_checklist_has_achieved_claim_and_tool_evidence(
            current,
            claimed=claimed,
            has_evidence=evidence,
        ):
            current = _complete_checklist(current)
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACHIEVED,
                reason=SessionGoalReason.CHECKLIST_COMPLETE_SAME_TURN,
            )
        elif claimed:
            nxt = current.next_checklist_item
            next_label = nxt[1] if nxt is not None else None
            done = len(current.completed & frozenset(range(len(current.checklist))))
            total = len(current.checklist)
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason=SessionGoalReason.achieved_ignored_incomplete(done, total, next_label),
            )
        elif _short_checklist_has_no_prior_progress_and_tool_answer(
            current,
            has_evidence=evidence,
            text=text,
            completed_before=completed_before,
        ):
            current = _complete_checklist(current)
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACHIEVED,
                reason=SessionGoalReason.CHECKLIST_COMPLETE_SAME_TURN,
            )
        else:
            done = len(current.completed & frozenset(range(len(current.checklist))))
            total = len(current.checklist)
            nxt = current.next_checklist_item
            if nxt is None:
                reason = SessionGoalReason.checklist_progress(done, total)
            else:
                reason = SessionGoalReason.checklist_progress(done, total, nxt[1])
            verdict = SessionGoalVerdict(status=SessionGoalStatus.ACTIVE, reason=reason)
    elif claimed:
        if current.host_owned or evidence:
            soft_host = _host_owned_achieved_claim_lacks_tool_evidence(
                current,
                has_evidence=evidence,
            )
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACHIEVED,
                reason=(
                    SessionGoalReason.ACHIEVED_HOST_SET
                    if soft_host
                    else SessionGoalReason.ACHIEVED_TOOL_EVIDENCE
                ),
            )
        else:
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason=SessionGoalReason.NO_TOOL_EVIDENCE,
            )
    else:
        if _host_owned_goal_has_unverified_cohort_reply(current, text):
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACHIEVED,
                reason=SessionGoalReason.ACHIEVED_HOST_SET,
            )
        elif _host_owned_goal_has_tool_evidence_and_answer_reply(
            current,
            text,
            has_evidence=evidence,
        ):
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACHIEVED,
                reason=SessionGoalReason.ACHIEVED_TOOL_EVIDENCE,
            )
        elif current.host_owned:
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason=SessionGoalReason.WAITING_HOST_SIGNAL,
            )
        else:
            verdict = SessionGoalVerdict(
                status=SessionGoalStatus.ACTIVE,
                reason=SessionGoalReason.WAITING_TOOL_EVIDENCE,
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
) -> str:
    """Loop-facing evaluate: status string; reason stored on the session goal."""
    return evaluate_session_goal(goal, result, session=session).status


__all__ = [
    "SessionGoalVerdict",
    "default_evaluate_session_goal",
    "evaluate_session_goal",
    "reply_claims_session_goal_achieved",
    "session_goal_reply_text",
    "turn_has_session_goal_evidence",
]
