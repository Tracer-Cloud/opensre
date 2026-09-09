"""SessionGoal component — cross-turn ``/goal`` continuation.

Distinct from the ReAct ``Goal`` / ``goal_review`` (one tool loop).
Surfaces and hosts import leaves they need, or curated names from this package.

Layout (import the leaf — this ``__init__`` must not create import cycles):

* :mod:`goal` — domain state, attach/clear, reason derive
* :mod:`judge` — cheap-model transcript verdict
* :mod:`validate` — cheap-model check of newly ticked items
* :mod:`evaluate` — host completion + evidence gate
* :mod:`progress` — progress / status-line formatting only
* :mod:`continuation` — session-goal continuation prompts
* :mod:`persist` — flush / restore
* :mod:`run_until` — ``run_until_session_goal``
"""

from __future__ import annotations

from core.agent_harness.session_goal.goal import (
    MAX_GOAL_CONDITION_CHARS,
    MAX_GOAL_REASON_CHARS,
    SessionGoal,
    SessionGoalReason,
    SessionGoalStatus,
    attach_session_goal,
    build_session_goal,
    clear_session_goal,
    derive_session_goal_reason,
    mark_session_goal_started,
    refresh_session_goal_reason,
    session_goal_elapsed_seconds,
    session_goal_is_active,
    session_goal_is_attached,
    session_goal_is_paused,
    session_goal_token_delta,
    strip_shell_prompt_chrome,
)
from core.agent_harness.session_goal.progress import (
    SESSION_GOAL_PROGRESS_MARK,
    SESSION_GOAL_USER_WORD,
    format_session_goal_progress,
    format_session_goal_status_line,
)
from core.agent_harness.session_goal.run_until import SessionGoalRunResult, run_until_session_goal

__all__ = [
    "MAX_GOAL_CONDITION_CHARS",
    "MAX_GOAL_REASON_CHARS",
    "SESSION_GOAL_PROGRESS_MARK",
    "SESSION_GOAL_USER_WORD",
    "SessionGoal",
    "SessionGoalReason",
    "SessionGoalRunResult",
    "SessionGoalStatus",
    "attach_session_goal",
    "build_session_goal",
    "clear_session_goal",
    "derive_session_goal_reason",
    "format_session_goal_progress",
    "format_session_goal_status_line",
    "mark_session_goal_started",
    "refresh_session_goal_reason",
    "run_until_session_goal",
    "session_goal_elapsed_seconds",
    "session_goal_is_active",
    "session_goal_is_attached",
    "session_goal_is_paused",
    "session_goal_token_delta",
    "strip_shell_prompt_chrome",
]
