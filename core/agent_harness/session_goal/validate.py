"""Cheap-model check of newly ticked SessionGoal checklist items.

Independent of the action model and of the whole-goal judge. Does not run tools.
A tick without supporting reply or tool evidence is rejected.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.llm.shared.structured_output import StructuredOutputClient
from core.llm.types import AgentLLMClient

log = logging.getLogger(__name__)

ItemVerdictName = Literal["VALID", "INVALID"]

MAX_REVIEWED_REPLY_CHARS = 4000

_VALIDATE_SYSTEM = (
    "You independently check whether newly ticked /goal checklist items "
    "were actually done.\n"
    "You do not run tools. Return JSON only.\n"
    "Set an item VALID only when the assistant reply plus successful tools "
    "clearly support that item.\n"
    "Set an item INVALID when the tick is a claim without supporting work.\n"
    "When in doubt, set INVALID."
)


class ChecklistItemVerdict(BaseModel):
    """One newly ticked checklist item."""

    index: int = Field(description="0-based checklist index")
    verdict: ItemVerdictName = Field(
        description="VALID when the item was done; INVALID when the tick is unsupported"
    )
    reason: str = Field(default="", description="One sentence for the host status line")


class ChecklistTickVerdict(BaseModel):
    """Per-item verdicts for ticks made this turn."""

    items: list[ChecklistItemVerdict] = Field(
        description="One verdict per newly ticked checklist index"
    )


class _AgentAsPromptClient:
    def __init__(self, llm: AgentLLMClient, *, system: str) -> None:
        self._llm = llm
        self._system = system

    def invoke(self, prompt: str) -> Any:
        return self._llm.invoke(
            [{"role": "user", "content": prompt}],
            system=self._system,
        )


def _ticked_block(ticked: tuple[tuple[int, str], ...]) -> str:
    lines = "\n".join(f"  - [{index}] {item}" for index, item in ticked)
    return f"Newly ticked checklist items:\n{lines}"


def invoke_checklist_tick_validator(
    llm: AgentLLMClient,
    *,
    condition: str,
    reply: str,
    evidence: bool,
    ticked: tuple[tuple[int, str], ...],
) -> ChecklistTickVerdict | None:
    """Return per-item verdicts, or ``None`` on transport / parse failure."""
    if not ticked:
        return ChecklistTickVerdict(items=[])
    prompt = (
        f"Goal condition:\n{condition}\n\n"
        f"Successful tool work this turn: {'yes' if evidence else 'no'}\n\n"
        f"{_ticked_block(ticked)}\n\n"
        f"Latest assistant reply:\n{reply[:MAX_REVIEWED_REPLY_CHARS]}\n\n"
        "Was each newly ticked item actually done?"
    )
    try:
        factory = getattr(llm, "with_structured_output", None)
        if callable(factory):
            parsed = factory(ChecklistTickVerdict).invoke(f"{_VALIDATE_SYSTEM}\n\n{prompt}")
        else:
            parsed = StructuredOutputClient(
                _AgentAsPromptClient(llm, system=_VALIDATE_SYSTEM),
                ChecklistTickVerdict,
            ).invoke(prompt)
    except Exception:
        log.debug("checklist tick validator LLM call failed", exc_info=True)
        return None

    if not isinstance(parsed, ChecklistTickVerdict):
        try:
            parsed = ChecklistTickVerdict.model_validate(parsed)
        except Exception:
            log.debug("checklist tick validator parse failed", exc_info=True)
            return None
    return parsed


def kept_tick_indices(
    parsed: ChecklistTickVerdict | None,
    *,
    newly: frozenset[int],
) -> frozenset[int]:
    """Indices the validator confirmed. Transport failure keeps the ticks.

    A tick the validator did not mention is not confirmed: an incomplete
    answer must not let an unsupported tick through.
    """
    if parsed is None:
        return newly
    confirmed = {item.index for item in parsed.items if item.verdict == "VALID"}
    return frozenset(index for index in newly if index in confirmed)


def rejected_tick_reasons(
    parsed: ChecklistTickVerdict | None,
    *,
    newly: frozenset[int],
) -> tuple[str, ...]:
    """Why each unconfirmed tick was refused, in index order, for the status line."""
    if parsed is None:
        return ()
    by_index = {item.index: item for item in parsed.items}
    reasons: list[str] = []
    for index in sorted(newly):
        item = by_index.get(index)
        if item is None:
            reasons.append(f"validator did not confirm item {index}")
        elif item.verdict == "INVALID":
            reasons.append(item.reason.strip() or f"item {index} not supported by the reply")
    return tuple(reasons)


__all__ = [
    "ChecklistItemVerdict",
    "ChecklistTickVerdict",
    "invoke_checklist_tick_validator",
    "kept_tick_indices",
    "rejected_tick_reasons",
]
