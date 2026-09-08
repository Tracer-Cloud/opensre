"""Cheap-model transcript judge for SessionGoal (met / not yet / impossible).

Independent of the action model. Does not run tools. ``GOAL_REACHED`` still
needs successful tool evidence — that gate lives in
:mod:`core.agent_harness.session_goal.evaluate`.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.llm.shared.structured_output import StructuredOutputClient
from core.llm.types import AgentLLMClient

log = logging.getLogger(__name__)

JudgeName = Literal["GOAL_REACHED", "NOT_REACHED", "IMPOSSIBLE"]

# The closing reply is enough for the verdict; a longer tail only costs tokens.
MAX_REVIEWED_REPLY_CHARS = 4000

_JUDGE_SYSTEM = (
    "You independently judge whether a /goal condition is met.\n"
    "You do not run tools. Return JSON only.\n"
    "Set verdict to GOAL_REACHED only when the assistant reply plus successful "
    "tools clearly satisfy the condition.\n"
    "Set verdict to NOT_REACHED when required work remains. Say the next "
    "concrete step in reason (for example which endpoint or check to use).\n"
    "Set verdict to IMPOSSIBLE when this session cannot meet the condition "
    "(missing access, contradicted facts, or the ask cannot be fulfilled).\n"
    "Unfinished checklist items mean NOT_REACHED unless the reply already "
    "satisfies the whole condition.\n"
    "A reply that contradicts itself is NOT_REACHED: a summary count that "
    "differs from its own table or list, a yes in a table row against a no "
    "in the text, or a total that does not match the rows. Name the "
    "contradiction in reason.\n"
    "When in doubt, set verdict to NOT_REACHED."
)


class SessionGoalJudgeVerdict(BaseModel):
    """Closed three-way transcript verdict plus a host-visible reason."""

    verdict: JudgeName = Field(
        description=(
            "GOAL_REACHED when the condition is met; NOT_REACHED when work "
            "remains; IMPOSSIBLE when this session cannot meet the condition"
        )
    )
    reason: str = Field(
        default="",
        description="One sentence the host shows the user and the next turn follows.",
    )


class _AgentAsPromptClient:
    """Adapt :class:`AgentLLMClient` message ``invoke`` to prompt-string ``invoke``."""

    def __init__(self, llm: AgentLLMClient, *, system: str) -> None:
        self._llm = llm
        self._system = system

    def invoke(self, prompt: str) -> Any:
        return self._llm.invoke(
            [{"role": "user", "content": prompt}],
            system=self._system,
        )


def default_classification_llm() -> Any:
    """Cheap classification-tier client for the transcript judge."""
    from core.llm.factory import LLMRole, get_llm

    return get_llm(LLMRole.CLASSIFICATION)


def _unfinished_block(unfinished: tuple[tuple[int, str], ...]) -> str:
    if not unfinished:
        return "Unfinished checklist items: none."
    lines = "\n".join(f"  - [{index}] {item}" for index, item in unfinished)
    return f"Unfinished checklist items:\n{lines}"


def invoke_session_goal_judge(
    llm: AgentLLMClient,
    *,
    condition: str,
    reply: str,
    evidence: bool,
    unfinished: tuple[tuple[int, str], ...] = (),
) -> SessionGoalJudgeVerdict | None:
    """Return the structured verdict, or ``None`` on transport / parse failure."""
    prompt = (
        f"Goal condition:\n{condition}\n\n"
        f"Successful tool work this turn: {'yes' if evidence else 'no'}\n\n"
        f"{_unfinished_block(unfinished)}\n\n"
        f"Latest assistant reply:\n{reply[:MAX_REVIEWED_REPLY_CHARS]}\n\n"
        "Is the goal reached, not yet, or impossible?"
    )
    try:
        factory = getattr(llm, "with_structured_output", None)
        if callable(factory):
            parsed = factory(SessionGoalJudgeVerdict).invoke(f"{_JUDGE_SYSTEM}\n\n{prompt}")
        else:
            parsed = StructuredOutputClient(
                _AgentAsPromptClient(llm, system=_JUDGE_SYSTEM),
                SessionGoalJudgeVerdict,
            ).invoke(prompt)
    except Exception:
        log.debug("session-goal judge LLM call failed", exc_info=True)
        return None

    if not isinstance(parsed, SessionGoalJudgeVerdict):
        try:
            parsed = SessionGoalJudgeVerdict.model_validate(parsed)
        except Exception:
            log.debug("session-goal judge parse failed", exc_info=True)
            return None
    return parsed


__all__ = [
    "MAX_REVIEWED_REPLY_CHARS",
    "JudgeName",
    "SessionGoalJudgeVerdict",
    "invoke_session_goal_judge",
]
