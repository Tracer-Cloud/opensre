"""Checks behind host-owned skill prerequisites, registered by id.

``config.constants.skill_prerequisites`` names, per skill, the check ids the
host runs before the skill starts. The capability that can answer a check (for
example, whether a GitHub REST token resolves) lives in ``integrations``,
which the gate in ``tools`` may not import; integrations register the check
here at boot and the gate looks it up by id.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

logger = logging.getLogger(__name__)

SkillPrerequisiteCheck = Callable[[Mapping[str, Any]], bool]
"""``(resolved_integrations) -> True`` when the prerequisite holds for a turn's integrations."""

_checks: dict[str, SkillPrerequisiteCheck] = {}


def register_skill_prerequisite_check(check_id: str, check: SkillPrerequisiteCheck) -> None:
    """Register ``check`` as the answer for ``check_id``, replacing an earlier registration."""
    key = check_id.strip()
    if not key:
        raise ValueError("a skill prerequisite check needs a non-empty id")
    _checks[key] = check


def registered_skill_prerequisite_checks() -> tuple[str, ...]:
    """Return the registered check ids, sorted."""
    return tuple(sorted(_checks))


def skill_prerequisite_verdict(
    check_id: str, resolved_integrations: Mapping[str, Any]
) -> bool | None:
    """Run the check registered as ``check_id``: its answer, or None when no check can answer.

    None covers an unregistered check and one that raises. Skill entry treats
    None as met (:func:`skill_prerequisite_met`); resuming work parked behind
    setup needs a True.
    """
    check = _checks.get(check_id)
    if check is None:
        logger.debug("No skill prerequisite check is registered as %r", check_id)
        return None
    try:
        return bool(check(resolved_integrations))
    except Exception:
        logger.warning("Skill prerequisite check %r failed", check_id, exc_info=True)
        return None


def skill_prerequisite_met(check_id: str, resolved_integrations: Mapping[str, Any]) -> bool:
    """Run the check registered as ``check_id`` on ``resolved_integrations``.

    Fails open: an unregistered check, or one that raises, counts as met, so a
    wiring gap or a bug in a check never locks a user out of a skill.
    """
    return skill_prerequisite_verdict(check_id, resolved_integrations) is not False


def clear_skill_prerequisite_checks() -> None:
    """Forget every registered check (process boot re-registers; tests reset)."""
    _checks.clear()


def reset() -> None:
    """Restore the empty registry (tests)."""
    clear_skill_prerequisite_checks()


__all__ = [
    "SkillPrerequisiteCheck",
    "clear_skill_prerequisite_checks",
    "register_skill_prerequisite_check",
    "registered_skill_prerequisite_checks",
    "skill_prerequisite_met",
    "skill_prerequisite_verdict",
]
