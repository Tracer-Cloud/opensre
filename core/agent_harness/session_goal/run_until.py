"""Session-goal continuation loop around ``chat`` for an active :class:`SessionGoal`.

One iteration = one ``chat`` turn (always through the action agent). Goals are
attached explicitly or by structured action handoff tags — never by scanning
user prose.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from core.agent_harness.session.pending_choice import PendingUserChoice
from core.agent_harness.session.terminal_access import (
    clear_pending_autosubmit,
    session_terminal,
    set_auto_command,
)
from core.agent_harness.session_goal.continuation import continuation_prompt
from core.agent_harness.session_goal.evaluate import (
    default_evaluate_session_goal,
    session_goal_reply_text,
    turn_has_session_goal_evidence,
)
from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalReason,
    SessionGoalStatus,
    attach_session_goal,
    refresh_session_goal_reason,
    session_goal_is_active,
    session_goal_is_paused,
)
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult

log = logging.getLogger(__name__)

ChatFn = Callable[[str], TurnResult]
EvaluateFn = Callable[..., str]
CancelFn = Callable[[], bool]
ProgressFn = Callable[[SessionGoal], None]


def _empty_turn_result() -> TurnResult:
    return TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
        ),
        assistant_response_text="",
    )


def _record_goal_turn(session: Any, active: SessionGoal) -> SessionGoal:
    """Count this chat as a session-goal turn and keep tool ticks on the session.

    ``session_goal_complete`` attaches ticks onto ``session.session_goal``.
    Recording the loop copy first would wipe them; merge them back so evaluate
    sees ``new_ticks`` and custom evaluate callbacks still advance ``turns_used``.
    """
    stored = getattr(session, "session_goal", None)
    completed = active.completed
    if isinstance(stored, SessionGoal):
        completed = completed | stored.completed
    updated = active.record_turn()
    if completed != updated.completed:
        updated = updated.with_completed(completed)
    attach_session_goal(session, updated)
    return updated


def _paint(
    session: Any,
    active: SessionGoal,
    on_progress: ProgressFn | None,
    *,
    rederive: bool = True,
) -> SessionGoal:
    """Notify the host of progress.

    When ``rederive`` is false, keep ``active.last_reason`` (e.g. the evaluate
    verdict) so we do not flash a stale "waiting…" line before "achieved".
    """
    painted = refresh_session_goal_reason(active) if rederive else active
    if not painted.last_reason.strip():
        painted = refresh_session_goal_reason(painted)
    attach_session_goal(session, painted)
    if on_progress is not None:
        on_progress(painted)
    return painted


def _announce_working(
    session: Any,
    active: SessionGoal,
    on_progress: ProgressFn | None,
) -> SessionGoal:
    """Paint a clear 'working now' line before a session-goal ``chat`` starts."""
    next_turn = min(active.turns_used + 1, active.max_outer_turns)
    working = active.with_reason(
        SessionGoalReason.working_session_turn(next_turn, active.max_outer_turns)
    )
    attach_session_goal(session, working)
    if on_progress is not None:
        on_progress(working)
    return working


_NO_PROGRESS_TURNS = 2
STALL_MENU_TITLE = "The goal made no progress in 2 turns. How should I continue?"
STALL_OPTION_MORE = "Keep going for one more turn"
STALL_OPTION_STOP = "Stop here; the work above is enough"
STALL_COMMANDS = {STALL_OPTION_MORE: "/goal resume", STALL_OPTION_STOP: "/goal clear"}


def goal_has_stalled(goal: SessionGoal) -> bool:
    """True when two turns passed with no checklist tick and no successful tool."""
    if goal.checklist_complete:
        return False
    return goal.turns_used - goal.last_progress_turns_used >= _NO_PROGRESS_TURNS


def pause_for_no_progress(
    session: Any, active: SessionGoal, on_progress: ProgressFn | None
) -> SessionGoal:
    """Pause a stalled goal; the shell also opens a menu with the ways forward.

    Two full turns without a tick or a successful tool means repeating the
    same steps to the budget. The interactive shell asks: one more turn, stop,
    or typed guidance (the custom row). Headless hosts have no ``/choose``
    handler, so they only pause and return.
    """
    paused = active.with_status(SessionGoalStatus.PAUSED).with_reason(
        SessionGoalReason.PAUSED_NO_PROGRESS
    )
    paused = _paint(session, paused, on_progress, rederive=False)
    _clear_host_autosubmit(session)
    if session_terminal(session) is None:
        return paused
    session.pending_user_choice = PendingUserChoice(
        title=STALL_MENU_TITLE,
        options=(STALL_OPTION_MORE, STALL_OPTION_STOP),
        commands=dict(STALL_COMMANDS),
    )
    set_auto_command(session, "/choose")
    return paused


def _chat_or_pause(
    chat: ChatFn, message: str, session: Any, on_progress: ProgressFn | None
) -> TurnResult:
    """Run one goal turn; when it raises, pause the goal before the error propagates.

    The host still prints the turn error. Without the pause the next message
    would resume the goal into the same failure (a credit wall, a rejected key)
    and burn its budget.
    """
    try:
        return chat(message)
    except Exception:
        active = getattr(session, "session_goal", None)
        if isinstance(active, SessionGoal) and active.status == SessionGoalStatus.ACTIVE:
            _pause_failed_turn(session, active, on_progress)
        raise


def _pause_failed_turn(
    session: Any, active: SessionGoal, on_progress: ProgressFn | None
) -> SessionGoal:
    """Pause the goal because its turn failed; state first, then the host paint.

    A failing paint must neither leave the goal active nor mask the turn
    error the caller is about to re-raise.
    """
    paused = active.with_status(SessionGoalStatus.PAUSED).with_reason(
        SessionGoalReason.PAUSED_TURN_FAILED
    )
    attach_session_goal(session, paused)
    _clear_host_autosubmit(session)
    try:
        _paint(session, paused, on_progress, rederive=False)
    except Exception:
        log.debug("session-goal pause paint failed", exc_info=True)
    return paused


def _turn_did_not_run(result: TurnResult) -> bool:
    """True when the action phase never ran: the driver caught the model call's failure.

    A rejected key or a provider outage comes back as a normal result marked
    ``not_run`` instead of an exception, so the loop must read the mark or it
    retries the same failure until the budget is gone.
    """
    return getattr(result.action_result, "accounting_status", "") == "not_run"


def _clear_host_autosubmit(session: Any) -> None:
    """Drop any queued shell autosubmit when the session goal stops continuing."""
    clear_pending_autosubmit(session)


def _finish_outer_turn(
    session: Any,
    active: SessionGoal,
    last: TurnResult,
    *,
    evaluate_fn: EvaluateFn,
    on_progress: ProgressFn | None,
) -> tuple[SessionGoal, TurnResult, bool]:
    """Evaluate → single paint. Returns ``(goal, result, stop)``."""
    if last.cancelled:
        active = active.with_status(SessionGoalStatus.CANCELLED)
        active = _paint(session, active, on_progress)
        _clear_host_autosubmit(session)
        return active, last, True

    if _turn_did_not_run(last):
        return _pause_failed_turn(session, active, on_progress), last, True

    if getattr(session, "pending_user_choice", None) is not None:
        active = active.with_reason(SessionGoalReason.PAUSED_USER_CHOICE)
        active = _paint(session, active, on_progress, rederive=False)
        return active, last, True

    next_status = evaluate_fn(active, last, session=session)
    stored = getattr(session, "session_goal", None)
    if isinstance(stored, SessionGoal):
        active = stored
    # After the reload, never before it: ``evaluate_fn`` re-attaches the goal
    # and taking the session copy would discard the finding. A continuation is
    # a fresh chat call and history carries prose only, so this is the only way
    # a later turn learns what earlier ones established.
    reply_text = session_goal_reply_text(last)
    if turn_has_session_goal_evidence(last):
        active = active.with_finding(reply_text)
        attach_session_goal(session, active)
    # Recorded even without tool evidence. Evidence gates *closing* the goal and
    # what counts as established; it must not gate whether the next turn knows
    # what this one said.
    if reply_text:
        active = active.with_last_answer(reply_text)
        attach_session_goal(session, active)
    # Evaluate return is authoritative — optional reviewers may keep ACTIVE after
    # structured evaluate briefly attached ACHIEVED on the session.
    if active.status != next_status:
        active = active.with_status(next_status)
        attach_session_goal(session, active)
    last = last

    if next_status != SessionGoalStatus.ACTIVE:
        active = _paint(session, active, on_progress, rederive=False)
        _clear_host_autosubmit(session)
        return active, last, True

    if active.turns_used >= active.max_outer_turns:
        active = active.with_status(SessionGoalStatus.BUDGET_EXHAUSTED)
        active = _paint(session, active, on_progress)
        _clear_host_autosubmit(session)
        return active, last, True

    if goal_has_stalled(active):
        active = pause_for_no_progress(session, active, on_progress)
        return active, last, True

    # Still active under budget: paint the verdict (the judge's reason) before
    # ``_announce_working`` paints the next turn's line. Hosts render both as
    # one-line status rows, so the reason is visible between turns.
    active = _paint(session, active, on_progress, rederive=False)
    return active, last, False


@dataclass(slots=True)
class SessionGoalRunResult:
    """Outcome of :func:`run_until_session_goal`."""

    goal: SessionGoal
    last_result: TurnResult
    turn_count: int


def run_until_session_goal(
    chat: ChatFn,
    session: Any,
    message: str,
    *,
    goal: SessionGoal | None = None,
    evaluate: EvaluateFn | None = None,
    cancel_requested: CancelFn | None = None,
    on_progress: ProgressFn | None = None,
) -> SessionGoalRunResult:
    """Run ``chat`` until the session goal is terminal or the budget is hit.

    Always runs the first ``chat(message)`` through the action-agent path.
    Continues with prompts only when a goal is already active afterward
    (explicit ``goal=`` attach, or ``session_goal:`` handoff from that turn).
    """
    evaluate_fn = evaluate or default_evaluate_session_goal

    if goal is not None:
        attach_session_goal(session, goal)

    pre = getattr(session, "session_goal", None)
    # User ``/goal pause``: allow one free chat, never session-goal continuation.
    if goal is None and isinstance(pre, SessionGoal) and pre.status == SessionGoalStatus.PAUSED:
        last = chat(message)
        stored = getattr(session, "session_goal", None)
        kept = stored if isinstance(stored, SessionGoal) else pre
        return SessionGoalRunResult(goal=kept, last_result=last, turn_count=kept.turns_used)

    had_active_before = False
    if isinstance(pre, SessionGoal) and pre.status == SessionGoalStatus.ACTIVE:
        had_active_before = True
        _announce_working(session, pre, on_progress)

    pre_chat_completed = pre.completed if isinstance(pre, SessionGoal) else frozenset()
    # Also covers a goal attached by ``session_goal_set`` inside this very turn:
    # the pause applies to whatever goal is active when the turn raises.
    last = _chat_or_pause(chat, message, session, on_progress)
    active = getattr(session, "session_goal", None)
    if not isinstance(active, SessionGoal) or not session_goal_is_active(session):
        # Paused after the first chat (e.g. slash during turn) — keep state.
        if isinstance(active, SessionGoal) and session_goal_is_paused(session):
            return SessionGoalRunResult(goal=active, last_result=last, turn_count=active.turns_used)
        synthetic = SessionGoal(
            condition=message.strip() or "(none)",
            max_outer_turns=1,
            status=SessionGoalStatus.CLEARED,
            turns_used=1,
        )
        return SessionGoalRunResult(goal=synthetic, last_result=last, turn_count=1)

    # ``/goal set`` attaches a host-owned goal mid-turn. The attach turn must
    # not count against the budget or run evaluate. The shell queues the
    # condition as the next REPL submit. Headless hosts have no REPL, so the
    # condition starts here as the first real session-goal turn.
    if not had_active_before and active.host_owned and active.turns_used == 0:
        if session_terminal(session) is not None:
            return SessionGoalRunResult(goal=active, last_result=last, turn_count=0)
        last = _chat_or_pause(chat, active.condition, session, on_progress)
        stored = getattr(session, "session_goal", None)
        if isinstance(stored, SessionGoal):
            active = stored

    if had_active_before or active.turns_used == 0:
        # This chat was a goal turn: the first one, or a resumed goal's next
        # one. Evaluate must see this-turn tool ticks as new, so re-read them
        # from the session instead of the pre-chat copy.
        active = replace(active, completed=pre_chat_completed, new_ticks=frozenset())
        active = _record_goal_turn(session, active)

    active, last, stop = _finish_outer_turn(
        session,
        active,
        last,
        evaluate_fn=evaluate_fn,
        on_progress=on_progress,
    )
    if stop:
        return SessionGoalRunResult(goal=active, last_result=last, turn_count=active.turns_used)

    while active.status == SessionGoalStatus.ACTIVE:
        if cancel_requested is not None and cancel_requested():
            active = active.with_status(SessionGoalStatus.CANCELLED)
            active = _paint(session, active, on_progress)
            _clear_host_autosubmit(session)
            break

        if active.turns_used >= active.max_outer_turns:
            active = active.with_status(SessionGoalStatus.BUDGET_EXHAUSTED)
            active = _paint(session, active, on_progress)
            _clear_host_autosubmit(session)
            break

        _announce_working(session, active, on_progress)
        last = _chat_or_pause(chat, continuation_prompt(active), session, on_progress)
        active = _record_goal_turn(session, active)
        active, last, stop = _finish_outer_turn(
            session,
            active,
            last,
            evaluate_fn=evaluate_fn,
            on_progress=on_progress,
        )
        if stop:
            break

    if last is None:
        last = _empty_turn_result()

    stored = getattr(session, "session_goal", None)
    if isinstance(stored, SessionGoal):
        active = stored

    return SessionGoalRunResult(
        goal=active,
        last_result=last,
        turn_count=active.turns_used,
    )


__all__ = [
    "SessionGoalRunResult",
    "run_until_session_goal",
    "session_goal_is_active",
]
