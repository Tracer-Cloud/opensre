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
    credits_lines = _hosted_credits_lines()
    surface_rules = ""
    if surface == "gateway" and turn_snapshot.interactive_choice_available:
        surface_rules = (
            "This is an unattended prompt on the hosted gateway: nobody watches this "
            "turn, and it cannot wait for a reply. When required input or a decision "
            "is missing, call ask_user_choice once with the exact question and its "
            "options; the turn then ends and resumes with the answer. Otherwise "
            "finish the work in this turn.\n"
        )
    if surface == "headless_cli":
        if turn_snapshot.interactive_choice_available:
            surface_rules = (
                "This is a non-interactive `opensre ask` invocation: do not describe "
                "it as the interactive shell and do not recommend slash commands. "
                "The process exits after this turn; use a required structured choice "
                "when user input is needed so the invocation can be resumed. On "
                "this surface, an available ask_user_choice is persisted and rendered "
                "after the turn instead of opening an in-process menu.\n"
            )
        else:
            surface_rules = (
                "This is a non-interactive, non-resumable `opensre ask` invocation: "
                "do not describe it as the interactive shell, recommend slash commands, "
                "or call ask_user_choice. If required input is missing, state what is "
                "needed in the final response instead of parking a choice.\n"
            )
    return (
        "TURN INTERACTION (authoritative for this turn):\n"
        f"- surface: {surface}\n"
        f"- session_goal: {goal}\n"
        f"{goal_lines}"
        f"{credits_lines}"
        f"- ask_user_choice menu: {menu}\n"
        f"{surface_rules}"
        "Optional follow-ups (run tests, commit, build next): call "
        "ask_user_choice only when the menu is available AND session_goal is "
        "none. Otherwise finish the work; one sentence of instructions is "
        "enough — do not park a numbered fallback no one will answer.\n"
    )


def _hosted_credits_lines() -> str:
    from core.llm.hosted_credits import hosted_credits_prompt_lines

    lines = hosted_credits_prompt_lines()
    if not lines:
        return ""
    return "".join(f"- {line}\n" for line in lines)


__all__ = ["turn_interaction_facts_block"]
