"""Session-goal continuation prompts for an attached SessionGoal.

Leaf module: imports :mod:`core.agent_harness.session_goal.goal` only —
do not import this from ``goal`` (avoids ``py/cyclic-import``). Distinct from
:mod:`core.agent_harness.session_goal.progress` (presentation).
"""

from __future__ import annotations

from core.agent_harness.session_goal.goal import (
    SessionGoal,
    derive_session_goal_reason,
)


def continuation_prompt(goal: SessionGoal) -> str:
    """User-visible follow-up message for the next session-goal turn."""
    reason = goal.last_reason.strip() or derive_session_goal_reason(goal)
    reason_block = f"Last progress: {reason}\n\n"
    if goal.findings:
        established = "\n".join(f"  - {item}" for item in goal.findings)
        reason_block += (
            "Already established in earlier turns of this goal — treat these as "
            "done and do not report them as unavailable:\n"
            f"{established}\n\n"
        )
    if goal.last_answer:
        reason_block += (
            "The previous turn of this goal already told the user:\n"
            f"  {goal.last_answer}\n"
            "Re-derive it if you must, but if your answer differs, say why — do "
            "not replace it with a different number silently.\n\n"
        )
    unfinished = goal.unfinished_items
    follow_reason = (
        "Follow the last progress reason. Do not claim the goal is met in prose — "
        "the host judge decides."
    )
    if unfinished:
        pending = "\n".join(f"  - [{index}] {item}" for index, item in unfinished)
        return (
            "[session_goal] Continue the active goal without asking whether to "
            f"continue. Goal: {goal.condition}\n\n"
            f"{reason_block}"
            "Unfinished checklist items (0-based indices):\n"
            f"{pending}\n\n"
            "Take the next unfinished item now. When you complete an item, call "
            f"session_goal_complete with that index. {follow_reason}"
        )
    return (
        "[session_goal] Continue the active goal without asking whether to "
        f"continue. Goal: {goal.condition}\n\n"
        f"{reason_block}"
        f"Take the next unfinished step now. {follow_reason}"
    )


__all__ = [
    "continuation_prompt",
]
