"""Session-goal continuation prompts for an attached SessionGoal.

Leaf module: imports goal + the contradiction helper. Do not import this
from ``goal`` (avoids ``py/cyclic-import``). Distinct from
:mod:`core.agent_harness.session_goal.progress` (presentation).
"""

from __future__ import annotations

from core.agent_harness.session_goal.goal import (
    SessionGoal,
    derive_session_goal_reason,
)
from core.agent_harness.session_goal.judge import judge_reason_is_contradiction

_SESSION_GOAL_MARK = "[session_goal]"
_USE_A_TOOL = (
    "Use a tool that can satisfy the condition now. "
    "Do not answer from memory or claim the work is already done."
)
_NEW_GOAL = "Earlier goals in this conversation are finished; do not continue their steps."


def start_goal_prompt(goal: SessionGoal, message: str) -> str:
    """First or resumed goal turn: keep the user text, require a tool.

    When the user text is the condition itself, it appears once, in the header.
    A goal on its first turn also says earlier goals are over, so their
    continuation prompts still in the conversation are not followed.
    """
    text = message.strip()
    if text.startswith(_SESSION_GOAL_MARK):
        return message
    header = f"{_SESSION_GOAL_MARK} Goal: {goal.condition}\n{_USE_A_TOOL}"
    if goal.turns_used == 0:
        header = f"{header}\nThis is a new goal. {_NEW_GOAL}"
    if text == goal.condition.strip():
        return header
    return f"{header}\n\n{text}"


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
        if judge_reason_is_contradiction(goal.last_reason):
            reason_block += (
                "The previous turn told the user something the judge flagged "
                "as a contradiction. Do not repeat that answer. Re-query with a "
                "tool and correct it:\n"
                f"  {goal.last_answer}\n\n"
            )
        else:
            reason_block += (
                "The previous turn of this goal already told the user:\n"
                f"  {goal.last_answer}\n"
                "Re-derive it if you must, but if your answer differs, say why — do "
                "not replace it with a different number silently.\n\n"
            )
    unfinished = goal.unfinished_items
    follow_reason = (
        f"{_USE_A_TOOL} Follow the last progress reason. Do not claim the goal "
        f"is met in prose — the host judge decides. {_NEW_GOAL}"
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
    "start_goal_prompt",
]
