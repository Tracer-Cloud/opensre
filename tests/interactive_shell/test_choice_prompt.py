"""Tests for the /choose slash command (pending ask_user_choice menu)."""

from __future__ import annotations

import io
import sys
from collections.abc import Callable

import pytest
from rich.console import Console

import surfaces.interactive_shell.command_registry.choice_prompt as choice_prompt
from config.constants.skills import (
    AUTOMATION_GROUP_OPTION,
    AUTOMATION_MENU_TITLE,
    CLOUD_REPAIR_OPTION,
    DEMO_REPO_DECLINE_OPTION,
    DEMO_REPO_PERMISSION_TITLE,
    LOCAL_REPAIR_OPTION,
    ONBOARDING_SKILL_NAME,
    SKIP_DEMO_OPTION,
    SLACK_OPTION,
)
from core.agent_harness.session.pending_choice import (
    AskUserQuestion,
    PendingUserChoice,
    format_ask_user_answers,
)
from core.agent_harness.task_plan import PlanStep, PlanStepStatus, TaskPlan
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.ask_user import CUSTOM_OPTION
from surfaces.interactive_shell.ui.input_prompt.rendering import resolve_prompt_placeholder
from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region
from tests.shared.terminal.pty_keyboard import TtyStringIO, pty_stdin

_CHOICE = PendingUserChoice(
    title="How should I handle the uncommitted changes?",
    options=(
        "Stash the changes (recommended – quick & safe)",
        "Commit the changes",
        "Use a separate git worktree",
    ),
)


def _console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def _handler(session: Session, console: Console) -> bool:
    return choice_prompt._cmd_choose(session, console, [])


def test_selection_is_auto_submitted_as_next_user_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.pending_user_choice = _CHOICE
    console, buf = _console()

    def _pick_second(**kwargs: object) -> str:
        assert kwargs["title"] == _CHOICE.title
        return "Commit the changes"

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", _pick_second)

    assert _handler(session, console) is True
    assert session.pending_user_choice is None
    # The question travels with the answer so the next turn cannot be re-routed.
    assert session.terminal.pending_prompt_default == format_ask_user_answers(
        _CHOICE.items(), ("Commit the changes",)
    )
    assert session.terminal.pending_prompt_autosubmit is True
    output = buf.getvalue()
    # Single-pick recap is the one-question Ask User card — not a ``↳`` line and
    # not a plan-step ``✓`` (that glued picks into Plan complete).
    assert "Ask User" in output
    assert "↳" not in output
    assert "offered:" not in output
    assert "✓" not in output
    assert _CHOICE.title in output
    assert "Commit the changes" in output


@pytest.mark.parametrize("answer", [SKIP_DEMO_OPTION, SLACK_OPTION])
def test_onboarding_labels_are_regular_answers_in_other_skills(
    monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    """Reserved onboarding labels must not alter an unrelated workflow."""
    title = "What should happen next?"
    pending = PendingUserChoice(title=title, options=(answer, "Not now"))
    session = Session()
    session.active_skill = "unrelated-workflow"
    session.pending_user_choice = pending
    plan = TaskPlan(steps=(PlanStep("Continue", PlanStepStatus.IN_PROGRESS),))
    session.task_plan = plan
    console, buf = _console()
    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", lambda **_kw: answer)

    assert _handler(session, console) is True

    assert session.active_skill == "unrelated-workflow"
    assert session.task_plan is plan
    assert session.terminal.pending_prompt_default == format_ask_user_answers(
        pending.items(), (answer,)
    )
    assert session.terminal.awaiting_handoff_answer is True
    assert session.questions_already_answered == {title.casefold()}
    assert title in buf.getvalue()
    assert "Opened the shell" not in buf.getvalue()


def test_direct_onboarding_leaf_keeps_the_question_that_offered_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback onboarding menus do not pretend a direct leaf came from the submenu."""
    title = "Choose a guided workflow"
    pending = PendingUserChoice(title=title, options=(SLACK_OPTION,))
    session = Session()
    session.active_skill = ONBOARDING_SKILL_NAME
    session.pending_user_choice = pending
    console, buf = _console()
    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", lambda **_kw: SLACK_OPTION)

    assert _handler(session, console) is True

    assert session.terminal.pending_prompt_default == format_ask_user_answers(
        pending.items(), (SLACK_OPTION,)
    )
    assert title in buf.getvalue()
    assert AUTOMATION_MENU_TITLE not in buf.getvalue()


@pytest.mark.parametrize("leaf", [LOCAL_REPAIR_OPTION, CLOUD_REPAIR_OPTION])
def test_direct_onboarding_repair_asks_permission_before_handoff(
    monkeypatch: pytest.MonkeyPatch,
    leaf: str,
) -> None:
    """Fallback menus retain the permission gate when repair leaves are direct."""
    title = "Choose a guided workflow"
    pending = PendingUserChoice(title=title, options=(leaf, SLACK_OPTION, SKIP_DEMO_OPTION))
    session = Session()
    session.active_skill = ONBOARDING_SKILL_NAME
    session.pending_user_choice = pending
    console, _buf = _console()
    picks = iter((leaf, DEMO_REPO_DECLINE_OPTION))
    titles: list[str] = []

    def choose(**kwargs: object) -> str:
        titles.append(str(kwargs["title"]))
        return next(picks)

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", choose)

    assert _handler(session, console) is True

    permission = AskUserQuestion(
        label="Demo repository",
        title=DEMO_REPO_PERMISSION_TITLE,
        options=(DEMO_REPO_DECLINE_OPTION,),
    )
    assert titles == [title, DEMO_REPO_PERMISSION_TITLE]
    assert session.terminal.pending_prompt_default == format_ask_user_answers(
        (pending.items()[0], permission),
        (leaf, DEMO_REPO_DECLINE_OPTION),
    )


def test_fallback_onboarding_label_does_not_open_the_automation_submenu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the shipped outcome menu treats its automation label as a group."""
    title = "Choose a guided workflow"
    pending = PendingUserChoice(
        title=title,
        options=(AUTOMATION_GROUP_OPTION, "Explore another workflow", SKIP_DEMO_OPTION),
    )
    session = Session()
    session.active_skill = ONBOARDING_SKILL_NAME
    session.pending_user_choice = pending
    console, _buf = _console()
    titles: list[str] = []

    def choose(**kwargs: object) -> str:
        titles.append(str(kwargs["title"]))
        return AUTOMATION_GROUP_OPTION

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", choose)

    assert _handler(session, console) is True

    assert titles == [title]
    assert session.terminal.pending_prompt_default == format_ask_user_answers(
        pending.items(), (AUTOMATION_GROUP_OPTION,)
    )


def test_selection_analytics_links_rendered_prompt_to_chosen_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.pending_user_choice = _CHOICE
    console, _buf = _console()
    rendered: list[dict[str, object]] = []
    answered: list[dict[str, object]] = []

    def pick(*, on_answer: Callable[[tuple[int, ...], str | None], None], **_kwargs: object) -> str:
        on_answer((1,), None)
        return "Commit the changes"

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(
        choice_prompt,
        "repl_choose_one",
        pick,
    )
    monkeypatch.setattr(
        choice_prompt,
        "capture_ask_user_prompt_rendered",
        lambda **properties: rendered.append(properties),
    )
    monkeypatch.setattr(
        choice_prompt,
        "capture_ask_user_prompt_answered",
        lambda **properties: answered.append(properties),
    )

    assert _handler(session, console) is True

    assert rendered[0]["interaction_id"] == answered[0]["interaction_id"]
    assert rendered[0]["render_mode"] == "picker"
    assert answered[0]["selected_option_indices"] == [(1,)]
    assert answered[0]["custom_answers"] == [None]
    assert answered[0]["disposition"] == "agent_answer"


def test_cancelled_menu_leaves_prompt_free(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session()
    session.pending_user_choice = _CHOICE
    console, buf = _console()

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", lambda **_kw: None)

    assert _handler(session, console) is True
    assert session.pending_user_choice is None
    assert session.terminal.pending_prompt_default is None
    assert session.terminal.pending_prompt_autosubmit is False
    assert "cancelled" in buf.getvalue().lower()


@pytest.mark.parametrize("plan_only", [False, True])
def test_cancelling_a_skill_menu_drops_the_skill_plan(
    monkeypatch: pytest.MonkeyPatch, plan_only: bool
) -> None:
    session = Session()
    plain_placeholder = resolve_prompt_placeholder(session)
    session.pending_user_choice = _CHOICE
    session.active_skill = "scheduling-github-ci-repairs"
    session.skills_already_prompted.add(session.active_skill)
    session.task_plan = TaskPlan(
        steps=(
            PlanStep(
                "Inspect CI failures",
                PlanStepStatus.PENDING if plan_only else PlanStepStatus.IN_PROGRESS,
            ),
            PlanStep("Schedule CI fixes", PlanStepStatus.PENDING),
        )
    )
    session.plan_only_until_authorized = plan_only
    state, spinner = ReplState(), SpinnerState()
    assert "Plan" in render_prompt_region(session, state, spinner).value
    assert resolve_prompt_placeholder(session) != plain_placeholder
    console, _buf = _console()
    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", lambda **_kw: None)

    assert _handler(session, console) is True

    assert session.active_skill is None
    assert "scheduling-github-ci-repairs" not in session.skills_already_prompted
    assert session.task_plan is None
    assert session.plan_only_until_authorized is False
    assert "Plan" not in render_prompt_region(session, state, spinner).value
    assert resolve_prompt_placeholder(session) == plain_placeholder


def test_cancelling_a_menu_without_a_skill_keeps_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session()
    session.pending_user_choice = _CHOICE
    plan = TaskPlan(
        steps=(
            PlanStep("Inspect CI failures", PlanStepStatus.IN_PROGRESS),
            PlanStep("Schedule CI fixes", PlanStepStatus.PENDING),
        )
    )
    session.task_plan = plan
    session.plan_only_until_authorized = True
    console, _buf = _console()
    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", lambda **_kw: None)

    assert _handler(session, console) is True

    assert session.active_skill is None
    assert session.task_plan is plan
    assert session.plan_only_until_authorized is True


def test_no_pending_choice_prints_notice() -> None:
    session = Session()
    console, buf = _console()

    assert _handler(session, console) is True
    assert "no selection menu is pending" in buf.getvalue().lower()


def test_non_tty_prints_options_as_text(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session()
    session.pending_user_choice = _CHOICE
    console, buf = _console()

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: False)

    assert _handler(session, console) is True
    output = buf.getvalue()
    assert _CHOICE.title in output
    for option in _CHOICE.options:
        assert option in output
    assert session.terminal.pending_prompt_default is None


def test_choose_is_registered_with_exclusive_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    import surfaces.interactive_shell.runtime.input_policy as input_policy
    from surfaces.interactive_shell.command_registry import SLASH_COMMANDS

    assert "/choose" in SLASH_COMMANDS

    # turn_needs_exclusive_stdin consults the module-level TTY check; force it
    # interactive so the registration (not the test environment) is asserted.
    monkeypatch.setattr(input_policy, "repl_tty_interactive", lambda: True)
    assert input_policy.turn_needs_exclusive_stdin("/choose", Session()) is True


_BATCH_QUESTIONS = (
    AskUserQuestion(
        label="Codebase",
        title="Where does the /api/orders service live?",
        options=("Hypothetical/demo scenario, no real code", "I'll point you at a repo"),
    ),
    AskUserQuestion(
        label="Window",
        title="What's the time window of the p99 regression?",
        options=("Last 7 days", "Last 24 hours"),
    ),
)
_BATCH_CHOICE = PendingUserChoice(
    title="Ask User",
    options=_BATCH_QUESTIONS[0].options,
    questions=_BATCH_QUESTIONS,
)


def test_batch_answers_are_auto_submitted_as_qa_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.pending_user_choice = _BATCH_CHOICE
    console, _buf = _console()
    answers = (
        "Hypothetical/demo scenario, no real code",
        "Last 7 days",
    )

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_ask_user", lambda _questions, **_kw: answers)
    monkeypatch.setattr(
        choice_prompt,
        "repl_choose_one",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("single menu must not run")),
    )

    assert _handler(session, console) is True
    assert session.pending_user_choice is None
    assert session.terminal.pending_prompt_autosubmit is True
    assert session.terminal.awaiting_handoff_answer is True
    assert session.terminal.pending_prompt_default == format_ask_user_answers(
        _BATCH_QUESTIONS, answers
    )


def test_batch_custom_option_is_captured_inline_and_auto_submitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Free text is an option: concrete answers auto-submit; no ``[N] ❯`` fill-in."""
    session = Session()
    session.pending_user_choice = _BATCH_CHOICE
    console, _buf = _console()
    answers = (
        "Hypothetical/demo scenario, no real code",
        "my custom window",
    )

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_ask_user", lambda _questions, **_kw: answers)

    assert _handler(session, console) is True
    assert session.terminal.pending_prompt_autosubmit is True
    assert session.terminal.awaiting_handoff_answer is True
    assert session.terminal.pending_prompt_default == format_ask_user_answers(
        _BATCH_QUESTIONS, answers
    )


def test_slash_command_typed_into_the_menu_runs_as_a_command_not_an_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the user types a slash command into the custom row.
    session = Session()
    session.pending_user_choice = _CHOICE
    console, buf = _console()
    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", lambda **_kw: "/loops messages")

    # Act
    assert _handler(session, console) is True

    # Assert: the menu closes, the command runs, nothing is handed to the model as an answer.
    assert session.terminal.pending_prompt_default == "/loops messages"
    assert session.terminal.awaiting_handoff_answer is False
    assert "Running /loops messages" in buf.getvalue()


def test_option_mapped_to_a_command_runs_that_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.agent_harness.session.pending_choice import PendingUserChoice

    session = Session()
    session.pending_user_choice = PendingUserChoice(
        title="How should I continue?",
        options=("Keep going", "Stop here"),
        commands={"Stop here": "/goal clear"},
    )
    console, buf = _console()
    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", lambda **_kw: "Stop here")

    assert _handler(session, console) is True

    assert session.terminal.pending_prompt_default == "/goal clear"
    assert session.terminal.awaiting_handoff_answer is False
    assert "Running /goal clear" in buf.getvalue()


def test_single_choice_types_custom_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.pending_user_choice = _CHOICE
    console, _buf = _console()
    seen: dict[str, object] = {}

    def _pick(**kwargs: object) -> str:
        seen["custom_label"] = kwargs.get("custom_label")
        seen["choices"] = kwargs["choices"]
        return "typed by hand"

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", _pick)

    assert _handler(session, console) is True
    assert seen["custom_label"] == CUSTOM_OPTION
    choices = seen["choices"]
    assert isinstance(choices, list)
    assert (CUSTOM_OPTION, CUSTOM_OPTION) in choices
    assert session.terminal.pending_prompt_default == format_ask_user_answers(
        _CHOICE.items(), ("typed by hand",)
    )
    assert session.terminal.pending_prompt_autosubmit is True


def test_single_choice_paints_the_pending_note_inside_the_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The explainer rides the menu paint (cleared on close), not the transcript."""
    session = Session()
    session.pending_user_choice = PendingUserChoice(
        title=_CHOICE.title,
        options=_CHOICE.options,
        note="Why I am asking, in one line.",
    )
    console, buf = _console()
    seen: dict[str, object] = {}

    def _pick(**kwargs: object) -> str:
        seen["note"] = kwargs.get("note")
        return _CHOICE.options[0]

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", _pick)

    assert _handler(session, console) is True
    assert seen["note"] == "Why I am asking, in one line."
    assert "Why I am asking" not in buf.getvalue()


def test_non_tty_batch_prints_every_question(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session()
    session.pending_user_choice = _BATCH_CHOICE
    console, buf = _console()

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: False)

    assert _handler(session, console) is True
    output = buf.getvalue()
    for question in _BATCH_QUESTIONS:
        assert question.title in output
        for option in question.options:
            assert option in output


def test_single_choice_without_custom_row_when_the_menu_disallows_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The startup demo menu declares allow_custom false: options only."""
    # Arrange
    session = Session()
    session.pending_user_choice = PendingUserChoice(
        title="Which demo?", options=("A", "B"), custom_answer=False
    )
    console, _buf = _console()
    seen: dict[str, object] = {}

    def _pick(**kwargs: object) -> str:
        seen["custom_label"] = kwargs.get("custom_label")
        seen["choices"] = kwargs["choices"]
        return "A"

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", _pick)

    # Act
    assert _handler(session, console) is True

    # Assert
    assert seen["custom_label"] is None
    assert (CUSTOM_OPTION, CUSTOM_OPTION) not in seen["choices"]  # type: ignore[operator]


def _record_dismissals(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Run the real picker on a terminal-like stdout; collect dismissal events."""
    dismissed: list[dict[str, object]] = []
    monkeypatch.setattr(sys, "stdout", TtyStringIO())
    monkeypatch.setattr(choice_prompt, "play_notification", lambda _event: None)
    monkeypatch.setattr(
        choice_prompt,
        "capture_ask_user_prompt_dismissed",
        lambda **properties: dismissed.append(properties),
    )
    return dismissed


def test_stray_terminal_input_never_cancels_the_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: a focus report, a CPR reply, a DA1 reply and Option+b arrive
    # before the user presses (B). Each one used to close the menu as Esc.
    session = Session()
    session.pending_user_choice = _CHOICE
    console, buf = _console()
    dismissed = _record_dismissals(monkeypatch)

    with pty_stdin(monkeypatch) as keyboard:
        keyboard.queue(b"\x1b[I", b"\x1b[12;1R", b"\x1b[?62;4c", b"\x1bb", b"b")

        # Act
        assert _handler(session, console) is True

    # Assert
    assert choice_prompt._CANCELLED not in buf.getvalue()
    assert dismissed == []
    assert session.terminal.pending_prompt_default == format_ask_user_answers(
        _CHOICE.items(), ("Commit the changes",)
    )


@pytest.mark.parametrize(
    ("pending", "keystroke", "dismiss_key"),
    [
        pytest.param(_CHOICE, b"\x1b", "esc", id="single-menu-esc"),
        pytest.param(_BATCH_CHOICE, b"\x03", "ctrl_c", id="batch-wizard-ctrl-c"),
    ],
)
def test_dismissal_analytics_name_the_key_that_closed_the_menu(
    monkeypatch: pytest.MonkeyPatch,
    pending: PendingUserChoice,
    keystroke: bytes,
    dismiss_key: str,
) -> None:
    session = Session()
    session.pending_user_choice = pending
    console, buf = _console()
    dismissed = _record_dismissals(monkeypatch)

    with pty_stdin(monkeypatch) as keyboard:
        keyboard.queue(keystroke)

        assert _handler(session, console) is True

    assert choice_prompt._CANCELLED in buf.getvalue()
    assert [(event["reason"], event["dismiss_key"]) for event in dismissed] == [
        ("cancelled", dismiss_key)
    ]
