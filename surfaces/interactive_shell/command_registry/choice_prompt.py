"""Slash command: open the pending interactive selection menu (``/choose``).

The ``ask_user_choice`` action tool stores a
:class:`~core.agent_harness.session.pending_choice.PendingUserChoice` on the
session and queues this command via ``set_auto_command``, so it runs as a
literal slash turn with exclusive stdin — the only place a raw-stdin arrow-key
picker is safe (see ``_EXCLUSIVE_STDIN_MENU_COMMANDS`` in
``runtime/input_policy.py``). The selected option label is auto-submitted
as the next user message so the agent receives the decision verbatim.
"""

from __future__ import annotations

from types import MappingProxyType

from rich.console import Console
from rich.markup import escape

from config.constants.skill_prerequisites import (
    PREREQUISITE_SKIPPED_NOTE,
    prerequisite_service_label,
)
from config.constants.skills import (
    AUTOMATION_GROUP_OPTION,
    AUTOMATION_MENU_OPTIONS,
    AUTOMATION_MENU_TITLE,
    DEMO_REPO_DECLINE_OPTION,
    DEMO_REPO_PERMISSION_TITLE,
    ONBOARDING_LEAF_CHOICES,
    ONBOARDING_SKILL_NAME,
    OUTCOME_MENU_OPTIONS,
    REPAIR_MENU_OPTIONS,
    SKIP_DEMO_OPTION,
)
from core.agent_harness.spi.handoff import (
    AskUserQuestion,
    format_ask_user_answers,
    question_key,
)
from core.agent_harness.spi.session_state import (
    PendingUserChoice,
    clear_pending_autosubmit,
    clear_setup_resume,
)
from core.agent_harness.spi.task_plan import discard_task_plan
from core.agent_harness.tools import ActionToolScope
from infrastructure.analytics.capture import (
    capture_ask_user_prompt_answered,
    capture_ask_user_prompt_dismissed,
    capture_ask_user_prompt_rendered,
)
from infrastructure.terminal import theme as ui_theme
from infrastructure.terminal.notify import NotifyEvent, play_notification
from surfaces.interactive_shell.command_registry.prerequisite_menu import (
    MenuStep,
    run_prerequisite_action,
)
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.runtime.startup.analysis_prefetch import (
    prefetch_after_menu_answer,
)
from surfaces.interactive_shell.runtime.startup.onboarding_telemetry import (
    capture_onboarding_choice,
)
from surfaces.interactive_shell.ui.ask_user import CUSTOM_OPTION, repl_ask_user
from surfaces.interactive_shell.ui.handoff_questions import render_choice_selections
from surfaces.interactive_shell.ui.prompt_visibility import clear_live_prompt_paint
from surfaces.shared.terminal.components.choice_menu import (
    print_valid_choice_list,
    repl_choose_one,
    repl_tty_interactive,
)
from tools.interactive_shell.actions.skill_entry import enter_skill
from tools.interactive_shell.actions.skill_prerequisite_gate import (
    hold_for_setup,
    is_prerequisite_menu,
    parse_prerequisite_action,
    setup_needed,
)

_CANCELLED = "Selection cancelled — type a reply instead."
_CHOOSE_COMMAND = "/choose"
# Onboarding leaf label -> the demo skill it starts.
_DEMO_BY_LEAF = MappingProxyType({label: name for name, label in ONBOARDING_LEAF_CHOICES})
_DEMO_SKIPPED = "Opened the shell — type a request, or /demo to come back to the menu."
_DEMO_UNAVAILABLE = "Guided demo selection is unavailable here — request a task directly."


def _analytics_questions(pending: PendingUserChoice) -> list[dict[str, object]]:
    items = pending.items()
    return [
        {
            "label": item.label,
            "title": item.title,
            "options": list(item.options),
            "multi_select": item.multi_select,
        }
        for item in items
    ]


def _capture_prompt_rendered(
    session: Session,
    pending: PendingUserChoice,
    *,
    render_mode: str,
) -> None:
    capture_ask_user_prompt_rendered(
        interaction_id=pending.interaction_id,
        questions=_analytics_questions(pending),
        render_mode=render_mode,
        allow_custom=bool(pending.custom_answer),
        has_command_options=bool(pending.commands),
        skill_name=session.active_skill,
        reason_code=pending.reason_code,
    )


def _capture_prompt_dismissed(
    pending: PendingUserChoice,
    *,
    skill_name: str | None,
    dismiss_keys: list[str],
) -> None:
    """Record the dismissal and the key class that closed the last picker, if known."""
    capture_ask_user_prompt_dismissed(
        interaction_id=pending.interaction_id,
        reason="cancelled",
        skill_name=skill_name,
        dismiss_key=dismiss_keys[-1] if dismiss_keys else None,
    )


def _remember_answered(session: Session, *titles: str) -> None:
    """Record questions the user has settled, so nothing asks them again."""
    settled = getattr(session, "questions_already_answered", None)
    if not isinstance(settled, set):
        return
    settled.update(question_key(title) for title in titles if title.strip())


def _leave_menu(session: Session, console: Console, note: str) -> None:
    """Close the menu with no answer for the model and leave the skill."""
    console.print(f"[{ui_theme.DIM}]{note}[/]")
    session.terminal.awaiting_handoff_answer = False
    # Nothing waits on setup once the user walked away from the menu.
    clear_setup_resume(session)
    if session.active_skill is not None:
        session.skills_already_prompted.discard(session.active_skill)
        discard_task_plan(session)
    session.active_skill = None


def _run_command(session: Session, console: Console, command: str) -> None:
    """Run a command picked from a menu as the next turn; it is not an answer for the model."""
    console.print(f"[{ui_theme.DIM}]Running {escape(command)}.[/]")
    session.terminal.awaiting_handoff_answer = False
    session.terminal.set_auto_command(command)


def _answer_prerequisite_menu(
    session: Session,
    console: Console,
    pending: PendingUserChoice,
    picked: str | None,
    *,
    selected_indices: list[tuple[int, ...]],
    custom_answers: list[str | None],
) -> bool:
    """Act on a pick from a skill prerequisite's setup menu; never an onboarding outcome."""
    if picked is None:
        capture_ask_user_prompt_dismissed(
            interaction_id=pending.interaction_id,
            reason="cancelled",
            skill_name=session.active_skill,
        )
        _leave_menu(session, console, _CANCELLED)
        return True
    capture_ask_user_prompt_answered(
        interaction_id=pending.interaction_id,
        selected_option_indices=selected_indices,
        custom_answers=custom_answers,
        disposition="command",
        skill_name=session.active_skill,
    )
    command = pending.commands.get(picked, "")
    action = parse_prerequisite_action(command)
    if action is None:
        # Local setup: the wizard resumes the parked turn when it finishes.
        if command:
            _run_command(session, console, command)
        return True
    name, service = action
    step = run_prerequisite_action(session, console, name, service)
    if step is MenuStep.LEAVE:
        label = prerequisite_service_label(service)
        _leave_menu(session, console, PREREQUISITE_SKIPPED_NOTE.format(service=label))
    elif step is MenuStep.ASK_AGAIN:
        return _show_queued_menu(session, console)
    return True


def _show_queued_menu(session: Session, console: Console) -> bool:
    """Show a menu queued from inside ``/choose`` in this same turn.

    A second autosubmitted ``/choose`` starts the prompt, which blocks in a raw
    read, so that menu would never paint. This turn already owns stdin.
    """
    if session.terminal.pending_prompt_default == _CHOOSE_COMMAND:
        clear_pending_autosubmit(session)
    return _cmd_choose(session, console, [])


def _enter_chosen_demo(session: Session, console: Console, leaf: str | None) -> None:
    """Enter the demo skill behind an onboarding ``leaf`` so its answer turn starts there.

    The answer turn then carries the skill's body and runs its first step,
    without a model round trip to load it. A demo whose setup is still missing
    is left to the model's own entry, which runs the prerequisite gate.
    """
    skill = _DEMO_BY_LEAF.get(leaf or "")
    if skill is None or setup_needed(session, skill):
        return
    enter_skill(skill, ActionToolScope(session=session, console=console, is_tty=True))


def _demo_needing_setup(session: Session, leaf: str | None) -> str | None:
    """The demo skill behind onboarding ``leaf`` when its prerequisites are unmet."""
    skill = _DEMO_BY_LEAF.get(leaf or "")
    return skill if skill is not None and setup_needed(session, skill) else None


def _cmd_choose(session: Session, console: Console, args: list[str]) -> bool:
    del args
    pending = session.pending_user_choice
    session.pending_user_choice = None
    if pending is None:
        console.print(f"[{ui_theme.DIM}]No selection menu is pending.[/]")
        return True

    if not repl_tty_interactive():
        _capture_prompt_rendered(session, pending, render_mode="text_fallback")
        if session.active_skill == ONBOARDING_SKILL_NAME:
            _leave_menu(session, console, _DEMO_UNAVAILABLE)
            return True
        for question in pending.items():
            print_valid_choice_list(
                console,
                title=question.title,
                choices=list(question.options),
            )
        console.print(f"[{ui_theme.DIM}]Reply with the option you want.[/]")
        return True

    items = pending.items()
    skill_name = session.active_skill
    is_onboarding = skill_name == ONBOARDING_SKILL_NAME
    is_outcome_menu = is_onboarding and items[0].options == OUTCOME_MENU_OPTIONS
    selected_indices: list[tuple[int, ...]] = [() for _ in items]
    custom_answers: list[str | None] = [None for _ in items]
    dismiss_keys: list[str] = []

    def remember_answer(index: int, indices: tuple[int, ...], custom: str | None) -> None:
        selected_indices[index] = indices
        custom_answers[index] = custom

    _capture_prompt_rendered(session, pending, render_mode="picker")
    clear_live_prompt_paint(session)
    play_notification(NotifyEvent.INPUT_NEEDED)  # the agent is now waiting on the user
    # Launch work held for the first wait starts now, settling behind this draw.
    session.terminal.release_startup_work()
    if pending.is_batch():
        picked = repl_ask_user(items, on_answer=remember_answer, on_dismiss=dismiss_keys.append)
        if picked is None:
            _capture_prompt_dismissed(pending, skill_name=skill_name, dismiss_keys=dismiss_keys)
            _leave_menu(session, console, _CANCELLED)
            return True
        capture_ask_user_prompt_answered(
            interaction_id=pending.interaction_id,
            selected_option_indices=selected_indices,
            custom_answers=custom_answers,
            disposition="agent_answer",
            skill_name=skill_name,
        )
        _remember_answered(session, *(question.title for question in items))
        session.terminal.set_auto_command(format_ask_user_answers(items, picked))
        session.terminal.awaiting_handoff_answer = True
        return True

    option_choices = [(option, option) for option in items[0].options]
    custom_label = CUSTOM_OPTION if pending.custom_answer else None
    if custom_label is not None:
        option_choices.append((custom_label, custom_label))
    custom_answer = False

    def mark_custom_answer() -> None:
        nonlocal custom_answer
        custom_answer = True

    def remember_single_answer(indices: tuple[int, ...], custom: str | None) -> None:
        remember_answer(0, indices, custom)

    # Custom row: type in place on the OpenSRE option array (Droid-style).
    picked_one = repl_choose_one(
        title=items[0].title,
        choices=option_choices,
        custom_label=custom_label,
        multi_select=items[0].multi_select,
        header="Ask User",
        letter_keys=True,
        note=pending.note,
        on_custom_answer=mark_custom_answer,
        on_answer=remember_single_answer,
        on_dismiss=dismiss_keys.append,
    )
    opening_answer: str | None = None
    permission_answer: str | None = None
    demo_needing_setup: str | None = None
    if picked_one == AUTOMATION_GROUP_OPTION and is_outcome_menu:
        # The group row opens a follow-up. The model receives the leaf, and a
        # repair leaf also receives the demo-repository permission.
        opening_answer = picked_one
        picked_one = repl_choose_one(
            title=AUTOMATION_MENU_TITLE,
            choices=[(option, option) for option in AUTOMATION_MENU_OPTIONS],
            custom_label=None,
            multi_select=False,
            header="Ask User",
            letter_keys=True,
            note="",
            on_dismiss=dismiss_keys.append,
        )
        # A demo still missing its setup (GitHub) asks for that first, before
        # the demo-repository question; the leaf answer resumes it afterwards.
        demo_needing_setup = _demo_needing_setup(session, picked_one)
    if is_onboarding and picked_one in REPAIR_MENU_OPTIONS and demo_needing_setup is None:
        create_option = _demo_create_option()
        permission_answer = repl_choose_one(
            title=DEMO_REPO_PERMISSION_TITLE,
            choices=[
                (create_option, create_option),
                (DEMO_REPO_DECLINE_OPTION, DEMO_REPO_DECLINE_OPTION),
            ],
            custom_label=None,
            multi_select=False,
            header="Ask User",
            letter_keys=True,
            note="",
        )
        if permission_answer is None:
            picked_one = None
    if is_prerequisite_menu(pending):
        return _answer_prerequisite_menu(
            session,
            console,
            pending,
            picked_one,
            selected_indices=selected_indices,
            custom_answers=custom_answers,
        )
    capture_onboarding_choice(session.active_skill, picked_one, custom=custom_answer)
    if picked_one is None:
        _capture_prompt_dismissed(pending, skill_name=skill_name, dismiss_keys=dismiss_keys)
        _leave_menu(session, console, _CANCELLED)
        return True
    command = pending.commands.get(picked_one) or (picked_one if picked_one.startswith("/") else "")
    if picked_one == SKIP_DEMO_OPTION and is_onboarding:
        disposition = "demo_skipped"
    elif command:
        disposition = "command"
    else:
        disposition = "agent_answer"
    capture_ask_user_prompt_answered(
        interaction_id=pending.interaction_id,
        selected_option_indices=selected_indices,
        custom_answers=custom_answers,
        disposition=disposition,
        skill_name=skill_name,
    )
    if picked_one == SKIP_DEMO_OPTION and is_onboarding:
        # A shell decision, not an answer for the model: the demo is over.
        _leave_menu(session, console, _DEMO_SKIPPED)
        return True

    if command:
        # A mapped option, or a slash command typed into the custom row, is a
        # command the shell runs, not an answer for the model.
        _remember_answered(session, items[0].title)
        _run_command(session, console, command)
        return True
    shown_title = AUTOMATION_MENU_TITLE if opening_answer is not None else items[0].title
    _remember_answered(session, items[0].title, shown_title)
    if permission_answer is not None:
        _remember_answered(session, DEMO_REPO_PERMISSION_TITLE)
    pairs = [(shown_title, picked_one)]
    if opening_answer is not None:
        pairs = [(items[0].title, opening_answer), (shown_title, picked_one)]
    if permission_answer is not None:
        pairs.append((DEMO_REPO_PERMISSION_TITLE, permission_answer))
    render_choice_selections(console, pairs)
    # The answer travels with its question, as the batched wizard's does: a bare
    # label such as "owner/repo (757 commits, CI configured)" reads to the
    # planner like a fresh request and gets re-asked or re-routed.
    questions = items
    answers: tuple[str, ...] = (picked_one,)
    if permission_answer is not None:
        questions = (
            items[0],
            AskUserQuestion(
                label="Demo repository",
                title=DEMO_REPO_PERMISSION_TITLE,
                options=(permission_answer,),
            ),
        )
        answers = (picked_one, permission_answer)
    answer = format_ask_user_answers(questions, answers)
    if demo_needing_setup is not None and hold_for_setup(session, demo_needing_setup, answer):
        return _show_queued_menu(session, console)
    if is_onboarding:
        _enter_chosen_demo(session, console, picked_one)
    prefetch_after_menu_answer(session, picked_one)
    session.terminal.set_auto_command(answer)
    session.terminal.awaiting_handoff_answer = True
    # ``render_choice_selections`` above already painted this answer's card.
    session.terminal.handoff_recap_text = answer.strip()
    return True


def _demo_create_option() -> str:
    """Permission row for one new private demo repository. No network call."""
    from integrations.github import fresh_demo_repo_name, saved_github_username

    repo = fresh_demo_repo_name()
    owner = saved_github_username()
    if owner:
        return f"Create {owner}/{repo}"
    return f"Create {repo}"


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/choose",
        "Open the pending interactive selection menu queued by the agent.",
        _cmd_choose,
        usage=("/choose",),
        # Renders the queued read-only picker; never mutates anything.
        mutating=False,
    )
]

__all__ = ["COMMANDS"]
