"""Progress / status-line formatting for SessionGoal.

Leaf module: presentation only. Domain reason derive lives in
:mod:`core.agent_harness.session_goal.goal`; continuation prompts in
:mod:`core.agent_harness.session_goal.continuation`. Do not import this from
``goal`` (avoids ``py/cyclic-import``).
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalReason,
    SessionGoalStatus,
    derive_session_goal_reason,
    session_goal_elapsed_seconds,
    session_goal_token_delta,
)
from infrastructure.evidence.evidence_compaction import truncate_message

# Leading mark for user-visible ``/goal`` progress lines (REPL + gateway).
SESSION_GOAL_PROGRESS_MARK = "◎"
# User-facing slash name — progress text never says ``SessionGoal``.
SESSION_GOAL_USER_WORD = "/goal"

# Status-line condition shares a Slack/Telegram timeline row with status,
# turn counter, and reason.
_MAX_STATUS_LINE_CONDITION_CHARS = 60


def format_duration_compact(seconds: float) -> str:
    """Human duration for status lines (``45s``, ``1m 23s``, ``1h 02m``)."""
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def format_token_count_compact(count: int) -> str:
    """Compact token count (``150``, ``1.2k``, ``3.4M``)."""
    value = max(0, int(count))
    if value < 1000:
        return str(value)
    # Branch on the rounded-to-one-decimal magnitude, not the raw value: a
    # count like 999_950 rounds to "1000.0" at .1f precision, and comparing
    # the raw value against 1_000_000 let that render as the broken "1000k"
    # instead of rolling into the M branch below.
    if round(value / 1000.0, 1) < 1000:
        scaled = value / 1000.0
        text = f"{scaled:.1f}".rstrip("0").rstrip(".")
        return f"{text}k"
    scaled = value / 1_000_000.0
    text = f"{scaled:.1f}".rstrip("0").rstrip(".")
    return f"{text}M"


def _headline(
    goal: SessionGoal,
    *,
    label: str,
    session: Any | None,
    now: float | None,
    input_tokens: int | None,
    output_tokens: int | None,
    reason: str,
) -> str:
    """One status line with elapsed time and token spend, for every status."""
    elapsed = session_goal_elapsed_seconds(goal, now=now)
    duration = format_duration_compact(elapsed) if elapsed is not None else "—"
    tokens = session_goal_token_delta(
        goal,
        session=session,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    token_text = format_token_count_compact(tokens)
    mark = SESSION_GOAL_PROGRESS_MARK
    word = SESSION_GOAL_USER_WORD
    working = " · working…" if label == "active" and SessionGoalReason.is_working(reason) else ""
    return (
        f"{mark} {word} {label}{working} · {duration} · "
        f"turn {goal.turns_used}/{goal.max_outer_turns} · +{token_text} tokens"
    )


# Status, ticks, checklist length, condition, start stamp.
GoalPaintSignature = tuple[str, frozenset[int], int, str, float | None]


def goal_paint_signature(goal: SessionGoal) -> GoalPaintSignature:
    """What a host repaints the full goal block for: status, ticks, checklist, identity."""
    return (goal.status, goal.completed, len(goal.checklist), goal.condition, goal.started_at)


def same_goal_identity(
    previous: GoalPaintSignature | None,
    current: GoalPaintSignature,
) -> bool:
    """True when both signatures describe the same attached goal (same start stamp)."""
    return previous is not None and previous[3:] == current[3:]


def _checklist_lines(goal: SessionGoal, *, indent: str, numbering_base: int) -> list[str]:
    next_index = goal.next_checklist_item[0] if goal.next_checklist_item else None
    lines: list[str] = []
    for index, item in enumerate(goal.checklist):
        done = index in goal.completed
        mark = "[x]" if done else "[ ]"
        prefix = "→ " if (not done and index == next_index) else "  "
        lines.append(f"{indent}{prefix}{mark} {index + numbering_base}. {item}")
    return lines


def format_session_goal_brief(goal: SessionGoal) -> str:
    """Condition, checklist and last verdict for the action prompt of a goal turn.

    Checklist indices are 0-based to match ``session_goal_complete``.
    """
    lines = [f"condition: {goal.condition}"]
    if goal.checklist:
        lines.append("checklist (tick finished items with session_goal_complete, 0-based index):")
        lines.extend(_checklist_lines(goal, indent="  ", numbering_base=0))
    reason = goal.last_reason.strip()
    if reason and not SessionGoalReason.is_working(reason):
        lines.append(f"last verdict: {reason}")
    return "\n".join(lines)


def format_session_goal_progress(
    goal: SessionGoal,
    *,
    session: Any | None = None,
    now: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    include_condition: bool = True,
) -> str:
    """Multi-line progress text for REPL mid-loop updates and ``/goal show``.

    ``include_condition`` is off for repaints of a goal the user has already
    seen, so the condition prints once per goal, not once per turn.
    """
    reason = goal.last_reason.strip() or derive_session_goal_reason(goal)
    headline = _headline(
        goal,
        label=goal.status,
        session=session,
        now=now,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reason=reason,
    )
    lines = [headline]
    if include_condition:
        lines.append(f"  condition: {goal.condition}")
    lines.append(f"  reason: {reason}")
    if not goal.checklist:
        return "\n".join(lines)
    lines.append("  Checklist:")
    lines.extend(_checklist_lines(goal, indent="  ", numbering_base=1))
    return "\n".join(lines)


def format_session_goal_status_line(
    goal: SessionGoal,
    *,
    session: Any | None = None,
    now: float | None = None,
) -> str:
    """Compact one-line status for gateway sinks (Slack/Telegram timelines)."""
    reason = goal.last_reason.strip() or derive_session_goal_reason(goal)
    condition = goal.condition.strip()
    condition = truncate_message(condition, _MAX_STATUS_LINE_CONDITION_CHARS)
    mark = SESSION_GOAL_PROGRESS_MARK
    word = SESSION_GOAL_USER_WORD
    if goal.status == SessionGoalStatus.ACTIVE:
        elapsed = session_goal_elapsed_seconds(goal, now=now)
        duration = format_duration_compact(elapsed) if elapsed is not None else "—"
        tokens = format_token_count_compact(session_goal_token_delta(goal, session=session))
        return (
            f"{mark} {word} active · {duration} · turn {goal.turns_used}/{goal.max_outer_turns} "
            f"· +{tokens} tok · {condition} · {reason}"
        )
    if goal.status == SessionGoalStatus.PAUSED:
        elapsed = session_goal_elapsed_seconds(goal, now=now)
        duration = format_duration_compact(elapsed) if elapsed is not None else "—"
        tokens = format_token_count_compact(session_goal_token_delta(goal, session=session))
        return (
            f"{mark} {word} paused · {duration} · turn {goal.turns_used}/{goal.max_outer_turns} "
            f"· +{tokens} tok · {condition} · {reason}"
        )
    return (
        f"{mark} {word} {goal.status} · turn {goal.turns_used}/{goal.max_outer_turns} · "
        f"{condition} · {reason}"
    )


def is_session_goal_progress_text(text: str) -> bool:
    """True when ``text`` is ``/goal`` status chrome, not an assistant answer.

    The ``/goal set`` attach turn may run ``slash_invoke`` (counts as tool
    evidence) and capture the progress status block as the turn "reply". Host
    evaluate must not treat that as a completed answer.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if SessionGoalReason.WAITING_HOST_SIGNAL in stripped:
        return True
    progress_lead = f"{SESSION_GOAL_PROGRESS_MARK} {SESSION_GOAL_USER_WORD}"
    return progress_lead in stripped


__all__ = [
    "GoalPaintSignature",
    "SESSION_GOAL_PROGRESS_MARK",
    "SESSION_GOAL_USER_WORD",
    "format_duration_compact",
    "format_session_goal_brief",
    "format_session_goal_progress",
    "format_session_goal_status_line",
    "format_token_count_compact",
    "goal_paint_signature",
    "is_session_goal_progress_text",
    "same_goal_identity",
]
