"""Whether a live task plan still blocks ending the action turn."""

from __future__ import annotations

from typing import Any

from config.constants.skills import ONBOARDING_LEAF_CHOICES, ONBOARDING_MENU_TITLE

_ONBOARDING_DEMO_SKILLS = frozenset(name for name, _label in ONBOARDING_LEAF_CHOICES)


def task_plan_blocks_conclusion(
    *,
    task_plan: Any | None,
    plan_only: bool,
) -> bool:
    """True when a live execution plan still requires work this turn.

    Plan-only (user asked not to run yet) never blocks. A settled plan — every
    step completed or blocked — never blocks: a blocked step has nothing left
    to run. Otherwise the agent must keep going — stopping with ``●`` on a
    mid-plan step leaves the shell idle while the overlay still shows work.
    """
    if plan_only or task_plan is None:
        return False
    steps = getattr(task_plan, "steps", None)
    if not steps:
        return False
    settled = getattr(task_plan, "is_settled", None)
    if callable(settled):
        return not bool(settled())
    if isinstance(settled, bool):
        return not settled
    return any(getattr(item, "status", None) not in {"completed", "blocked"} for item in steps)


def blocked_steps_await_the_user(session: Any, *, user_answered: bool = False) -> bool:
    """True when a step was newly blocked this turn and the user has not been asked about it.

    A blocked step is resolved with the user, not skipped: the turn ends
    through a question (``ask_user_choice`` queued), never on the block alone.
    On a turn that carries the user's answer they have just been consulted,
    so a step that stays blocked by their choice ends the turn.
    """
    from core.agent_harness.task_plan.evidence import blocked_this_turn

    if user_answered or not blocked_this_turn(session):
        return False
    return getattr(session, "pending_user_choice", None) is None


def demo_entered_from_menu(skill: str | None, message: str) -> bool:
    """True when ``message`` answers the onboarding menu and ``skill`` is a demo it starts.

    The shell enters the chosen demo when the pick is made, so that answer
    turn starts in the demo itself rather than in the onboarding master.
    """
    from core.agent_harness.session.pending_choice import parse_ask_user_answers

    if skill not in _ONBOARDING_DEMO_SKILLS:
        return False
    return any(question == ONBOARDING_MENU_TITLE for question, _ in parse_ask_user_answers(message))


def demo_pick_stalled_on_skill_load(
    session: Any,
    *,
    user_answered: bool,
    from_onboarding_menu: bool,
    entered_from_menu: bool = False,
) -> bool:
    """True when the onboarding menu's answer only loaded the chosen demo skill.

    The answer to "What would you like to do?" is the go-ahead. A
    turn that loads the chosen skill and then stops — no plan written, no
    step run, no menu queued — has stalled; the first demo did exactly that
    live. When the shell already entered the demo (``entered_from_menu``)
    nothing is loaded, so a turn with no tool return has stalled. A hand-off
    between two workflow skills is not this: the next skill starts on its
    own terms.
    """
    from core.agent_harness.task_plan.evidence import no_tool_returned, skill_loaded_without_work

    if not user_answered:
        return False
    if entered_from_menu:
        stalled = no_tool_returned(session)
    elif from_onboarding_menu:
        stalled = skill_loaded_without_work(session)
    else:
        return False
    return stalled and getattr(session, "pending_user_choice", None) is None


def task_plan_awaits_reply(*, task_plan: Any | None) -> bool:
    """True when the plan's current or next step is a ``deliverable`` text reply.

    This is the explicit signal that lets a plan-rejected conclusion reach the
    user; a plan without it keeps every rejected reply off the screen.
    """
    return task_plan is not None and getattr(task_plan, "awaits_reply", False) is True


__all__ = [
    "blocked_steps_await_the_user",
    "demo_entered_from_menu",
    "demo_pick_stalled_on_skill_load",
    "task_plan_awaits_reply",
    "task_plan_blocks_conclusion",
]
