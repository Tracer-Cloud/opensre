"""Record the CI performance insight only after its reply reaches the output sink."""

from __future__ import annotations

import re

from config.constants.skills import ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME
from core.agent_harness.ports import SessionState
from infrastructure.analytics.capture import capture_skill_value_delivered
from infrastructure.analytics.repl_context import get_prompt_turn_id

_INSIGHT = re.compile(
    r"^[#*_ \t]*What insights stand out[:*_ \t]*\r?\n[ \t\r\n]*[-*•][ \t]+([^\r\n]+)",
    re.IGNORECASE | re.MULTILINE,
)
_PLACEHOLDER = re.compile(r"\bx\.x\b|\bxx\b|<[^>]+>")


def ci_performance_insight(text: str) -> str:
    """Extract the first insight bullet from the report without rewriting it."""
    match = _INSIGHT.search(text)
    if match is None:
        return ""
    lines = [match.group(1).strip()]
    for line in text[match.end() :].splitlines()[1:]:
        if not line.strip() or not line.startswith((" ", "\t")):
            break
        if re.match(r"^\s*(?:[-*•]\s|#|```)", line):
            break
        lines.append(line.strip())
    insight = " ".join(lines)
    return "" if _PLACEHOLDER.search(insight) else insight


def record_skill_value(session: SessionState, text: str, seen: set[str]) -> None:
    """Capture one displayed insight per turn, preserving already delivered replies on cancellation."""
    if getattr(session, "active_skill", None) != ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME:
        return
    insight = ci_performance_insight(text)
    if not insight or insight in seen:
        return
    capture_skill_value_delivered(
        skill_name=ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
        insight=insight,
        prompt_turn_id=get_prompt_turn_id(),
    )
    seen.add(insight)
