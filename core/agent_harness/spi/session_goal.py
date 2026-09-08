"""Attach, query, render and drive a multi-turn session goal."""

from __future__ import annotations

from core.agent_harness.session_goal.goal import (
    MAX_GOAL_CONDITION_CHARS,
    SessionGoal,
    SessionGoalReason,
    SessionGoalStatus,
    attach_session_goal,
    build_session_goal,
    clear_session_goal,
    derive_session_goal_checklist,
    session_goal_is_active,
    session_goal_is_attached,
    session_goal_is_paused,
)
from core.agent_harness.session_goal.progress import (
    GoalPaintSignature,
    format_session_goal_progress,
    format_session_goal_status_line,
    goal_paint_signature,
    same_goal_identity,
)
from core.agent_harness.session_goal.run_until import run_until_session_goal

__all__ = [
    "GoalPaintSignature",
    "MAX_GOAL_CONDITION_CHARS",
    "SessionGoal",
    "SessionGoalReason",
    "SessionGoalStatus",
    "attach_session_goal",
    "build_session_goal",
    "clear_session_goal",
    "derive_session_goal_checklist",
    "format_session_goal_progress",
    "format_session_goal_status_line",
    "goal_paint_signature",
    "run_until_session_goal",
    "same_goal_identity",
    "session_goal_is_active",
    "session_goal_is_attached",
    "session_goal_is_paused",
]
