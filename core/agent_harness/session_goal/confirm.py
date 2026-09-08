"""Inject a specific LLM as the SessionGoal transcript judge.

The default loop uses the classification-tier client. Tests and hosts that
already hold an :class:`~core.llm.types.AgentLLMClient` pass it here.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.session_goal.evaluate import evaluate_session_goal
from core.agent_harness.session_goal.goal import SessionGoal
from core.llm.types import AgentLLMClient


def build_session_goal_llm_evaluator(llm: AgentLLMClient):
    """Return an ``evaluate(goal, result, *, session=) -> status`` for the loop."""

    def _evaluate(
        goal: SessionGoal,
        result: Any,
        *,
        session: Any | None = None,
    ) -> str:
        return evaluate_session_goal(goal, result, session=session, judge_llm=llm).status

    return _evaluate


__all__ = [
    "build_session_goal_llm_evaluator",
]
