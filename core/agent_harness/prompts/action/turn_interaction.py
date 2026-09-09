"""Per-turn interaction facts for Ask User / optional follow-up policy."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.agent_harness.turns.turn_snapshot import TurnSnapshot


def turn_interaction_facts_block(turn_snapshot: TurnSnapshot) -> str:
    """Authoritative surface / goal / menu facts for this turn.

    The STABLE system prompt tells the model when not to park optional
    follow-ups on ``ask_user_choice``. That rule is useless unless these
    facts are present in the same prompt.
    """
    surface = turn_snapshot.prompt_surface or "unknown"
    goal = "attached" if turn_snapshot.session_goal_attached else "none"
    menu = "available" if turn_snapshot.interactive_choice_available else "unavailable"
    brief = turn_snapshot.session_goal_brief.strip()
    goal_lines = "".join(f"  {line}\n" for line in brief.splitlines()) if brief else ""
    return (
        "TURN INTERACTION (authoritative for this turn):\n"
        f"- surface: {surface}\n"
        f"- session_goal: {goal}\n"
        f"{goal_lines}"
        f"- ask_user_choice menu: {menu}\n"
        "Optional follow-ups (run tests, commit, build next): call "
        "ask_user_choice only when the menu is available AND session_goal is "
        "none. Otherwise finish the work; one sentence of instructions is "
        "enough — do not park a numbered fallback no one will answer.\n"
    )


__all__ = ["turn_interaction_facts_block"]
