"""Record a skill's insight only after its reply reaches the output sink."""

from __future__ import annotations

import re

from config.constants.local_insights import LOCAL_INSIGHT_LABELS
from config.constants.skills import (
    ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
    ANALYZING_LOCAL_REPOSITORIES_SKILL_NAME,
)
from core.agent_harness.ports import SessionState
from infrastructure.analytics.capture import capture_skill_value_delivered
from infrastructure.analytics.repl_context import get_prompt_turn_id

_INSIGHT = re.compile(
    r"^[#*_ \t]*What insights stand out[:*_ \t]*\r?\n[ \t\r\n]*[-*•][ \t]+([^\r\n]+)",
    re.IGNORECASE | re.MULTILINE,
)
_PLACEHOLDER = re.compile(r"\bx\.x\b|\bxx\b|<[^>]+>")
# The local report's first "What stands out" bullet, which opens with its bold label.
_LOCAL_LEAD = re.compile(
    r"^[#*_ \t]*What stands out[:*_ \t]*\r?\n[ \t\r\n]*[-*•][ \t]+\*\*(?P<label>[^*\r\n]+)\*\*",
    re.IGNORECASE | re.MULTILINE,
)
_LOCAL_LABELS = frozenset(label.casefold() for label in LOCAL_INSIGHT_LABELS.values())


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


def local_insight_label(text: str) -> str:
    """The label that opens the local report's first ``What stands out`` bullet, or empty."""
    match = _LOCAL_LEAD.search(text)
    if match is None:
        return ""
    label = match.group("label").strip().rstrip(":.").strip()
    return label if label.casefold() in _LOCAL_LABELS else ""


def record_skill_value(session: SessionState, text: str, seen: set[str]) -> None:
    """Capture one displayed insight per turn, preserving already delivered replies on cancellation."""
    skill = getattr(session, "active_skill", None)
    if skill == ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME:
        _record_ci_insight(text, seen)
    elif skill == ANALYZING_LOCAL_REPOSITORIES_SKILL_NAME:
        _record_local_insight(session, text, seen)


def _record_ci_insight(text: str, seen: set[str]) -> None:
    insight = ci_performance_insight(text)
    if not insight or insight in seen:
        return
    capture_skill_value_delivered(
        skill_name=ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
        insight=insight,
        prompt_turn_id=get_prompt_turn_id(),
    )
    seen.add(insight)


def _record_local_insight(session: SessionState, text: str, seen: set[str]) -> None:
    """Record the summary the analysis tool left for the bullet's label, never the reply text.

    The reply names the user's repositories; the stored summary does not.
    """
    label = local_insight_label(text)
    notes = getattr(session, "skill_value_notes", None)
    if not label or not isinstance(notes, dict):
        return
    note = next((value for key, value in notes.items() if key.casefold() == label.casefold()), None)
    if note is None:
        return
    kind, summary = note
    insight = f"{label}: {summary}"
    if insight in seen:
        return
    capture_skill_value_delivered(
        skill_name=ANALYZING_LOCAL_REPOSITORIES_SKILL_NAME,
        insight=insight,
        prompt_turn_id=get_prompt_turn_id(),
        insight_kind=kind,
    )
    seen.add(insight)
