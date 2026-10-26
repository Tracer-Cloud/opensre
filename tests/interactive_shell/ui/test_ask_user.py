"""Ask User wizard: breadcrumb and key loop."""

from __future__ import annotations

import pytest

from core.agent_harness.session.pending_choice import (
    AskUserQuestion,
    format_ask_user_answers,
    parse_ask_user_answers,
)
from surfaces.interactive_shell.ui.ask_user import format_ask_user_breadcrumb, repl_ask_user

_QUESTIONS = (
    AskUserQuestion(
        label="Codebase",
        title="Where does the /api/orders service live?",
        options=("Hypothetical/demo scenario, no real code", "I'll point you at a repo"),
    ),
    AskUserQuestion(
        label="Metrics",
        title="How should I get the p99 latency data?",
        options=("I'll paste the raw numbers/graph description", "Query Datadog"),
    ),
    AskUserQuestion(
        label="Window",
        title="What's the time window of the p99 regression?",
        options=("Last 7 days", "Last 24 hours"),
    ),
)


def _patch_wizard(monkeypatch, actions: list[str]) -> None:
    keys = iter(actions)
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user.repl_tty_interactive",
        lambda: True,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user.read_menu_or_char",
        lambda **_kwargs: next(keys),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user._draw_ask_user",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user._leave_ask_user",
        lambda _question: None,
    )
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.cpr_stdin.drain_stale_cpr_bytes",
        lambda: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user.flush_pending_input",
        lambda: None,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user.clear_live_prompt_paint",
        lambda: None,
    )


def test_breadcrumb_hollow_until_a_question_is_replied() -> None:
    crumb = format_ask_user_breadcrumb(
        _QUESTIONS,
        answered=(False, False, False),
    )
    assert crumb == "○ Codebase → ○ Metrics → ○ Window"


def test_breadcrumb_fills_only_replied_questions() -> None:
    crumb = format_ask_user_breadcrumb(
        _QUESTIONS,
        answered=(True, False, False),
    )
    assert crumb == "● Codebase → ○ Metrics → ○ Window"


def test_answer_block_round_trips() -> None:
    answers = (
        "Hypothetical/demo scenario, no real code",
        "I'll paste the raw numbers/graph description",
        "Last 7 days",
    )
    text = format_ask_user_answers(_QUESTIONS, answers)
    parsed = parse_ask_user_answers(text)
    assert parsed == list(zip((q.title for q in _QUESTIONS), answers, strict=True))


def test_answer_block_round_trips_a_multiline_custom_answer() -> None:
    answers = ("First paragraph\n\n2. A numbered paragraph",)
    text = format_ask_user_answers((_QUESTIONS[0],), answers)

    assert parse_ask_user_answers(text) == [(_QUESTIONS[0].title, answers[0])]


def test_legacy_answer_keeps_numbered_lines_inside_one_answer() -> None:
    text = f"1. {_QUESTIONS[0].title}\nA numbered list:\n2. Option A\nMore details"

    assert parse_ask_user_answers(text) == [
        (_QUESTIONS[0].title, "A numbered list:\n2. Option A\nMore details")
    ]


def test_wizard_enter_on_each_question_submits(monkeypatch) -> None:
    _patch_wizard(monkeypatch, ["enter", "enter", "enter"])
    picked = repl_ask_user(_QUESTIONS)
    assert picked == (
        "Hypothetical/demo scenario, no real code",
        "I'll paste the raw numbers/graph description",
        "Last 7 days",
    )


def test_wizard_esc_cancels(monkeypatch) -> None:
    _patch_wizard(monkeypatch, ["cancel"])
    assert repl_ask_user(_QUESTIONS) is None


def test_wizard_reports_listed_indexes_and_custom_text_without_matching_labels(monkeypatch) -> None:
    questions = (
        AskUserQuestion(label="One", title="Pick", options=("other", "two\nlines")),
        AskUserQuestion(label="Two", title="Type", options=("same", "other")),
    )
    _patch_wizard(monkeypatch, ["B", "down", "down", *"same", "enter"])
    answers: list[tuple[int, tuple[int, ...], str | None]] = []

    def remember_answer(index: int, indices: tuple[int, ...], custom: str | None) -> None:
        answers.append((index, indices, custom))

    assert repl_ask_user(questions, on_answer=remember_answer) is not None
    assert answers == [(0, (1,), None), (1, (), "same")]


def test_wizard_labels_options_with_letters(monkeypatch) -> None:
    # Arrange: capture the drawn panel for the first question.
    import io
    import re
    import sys

    from surfaces.interactive_shell.ui import ask_user

    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(ask_user, "menu_columns", lambda: 80)

    # Act
    ask_user._draw_ask_user(
        questions=_QUESTIONS,
        current=0,
        answers=[None, None, None],
        option_index=0,
        erase_lines=0,
    )

    # Assert: (A)/(B) chips and a letter-range hint, no numeric labels.
    plain = re.sub(r"\x1b\[[0-9;:]*[A-Za-z]", "", out.getvalue())
    assert "(A) Hypothetical/demo scenario, no real code" in plain
    assert "(B) I'll point you at a repo" in plain
    assert "Enter/A-C Select" in plain
    assert "1." not in plain


def test_wizard_selects_option_by_letter_key(monkeypatch) -> None:
    # Pressing (B) on each question chooses the second option without arrows.
    _patch_wizard(monkeypatch, ["B", "B", "B"])
    picked = repl_ask_user(_QUESTIONS)
    assert picked == (
        "I'll point you at a repo",
        "Query Datadog",
        "Last 24 hours",
    )


def test_wizard_flushes_leftover_keys_before_reading(monkeypatch) -> None:
    flushed = {"count": 0}

    def _flush() -> None:
        flushed["count"] += 1

    _patch_wizard(monkeypatch, ["enter", "enter", "enter"])
    monkeypatch.setattr("surfaces.interactive_shell.ui.ask_user.flush_pending_input", _flush)

    assert repl_ask_user(_QUESTIONS) is not None
    assert flushed["count"] >= 2


def test_wizard_submit_row_confirms_highlighted_option(monkeypatch) -> None:
    # First question: 2 options + custom = 3 rows + Submit. Up wraps to Submit.
    _patch_wizard(monkeypatch, ["up", "enter", "enter", "enter"])
    picked = repl_ask_user(_QUESTIONS)
    assert picked == (
        "Hypothetical/demo scenario, no real code",
        "I'll paste the raw numbers/graph description",
        "Last 7 days",
    )


def test_wizard_types_on_custom_row_in_place(monkeypatch) -> None:
    """Droid-style: typed text is the last row of the OpenSRE option array."""
    # Down to custom row, type "paste", Enter, then Enter on Q2/Q3 defaults.
    _patch_wizard(
        monkeypatch,
        ["down", "down", "p", "a", "s", "t", "e", "enter", "enter", "enter"],
    )
    picked = repl_ask_user(_QUESTIONS)
    assert picked == (
        "paste",
        "I'll paste the raw numbers/graph description",
        "Last 7 days",
    )
    assert "Or type your own" not in "".join(picked)


def test_wizard_multi_select_toggles_and_submits(monkeypatch) -> None:
    """Checkboxes: Space toggles; Submit commits newline-joined labels."""
    questions = (
        AskUserQuestion(
            label="Extras",
            title="Which extras?",
            options=("Unit tests", "Dockerfile", "CI workflow"),
            multi_select=True,
        ),
        AskUserQuestion(
            label="Lang",
            title="Language?",
            options=("Python", "Go"),
        ),
    )
    # Toggle option 0 and 1, down to Submit, Enter; then Enter on Q2 default.
    _patch_wizard(
        monkeypatch,
        [" ", "down", " ", "down", "down", "down", "enter", "enter"],
    )
    picked = repl_ask_user(questions)
    assert picked == ("Unit tests\nDockerfile", "Python")


def test_wizard_restores_terminal_when_draw_raises(monkeypatch) -> None:
    restored: list[bool] = []

    def _restore() -> None:
        restored.append(True)

    def _boom(**_kwargs: object) -> None:
        raise RuntimeError("draw failed")

    _patch_wizard(monkeypatch, [])
    monkeypatch.setattr(
        "surfaces.interactive_shell.ui.ask_user._draw_ask_user",
        _boom,
    )
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.key_reader.restore_stdin_terminal",
        _restore,
    )

    with pytest.raises(RuntimeError, match="draw failed"):
        repl_ask_user(_QUESTIONS)

    assert restored == [True]


def test_draw_ask_user_multi_uses_checkboxes(monkeypatch) -> None:
    import io
    import re
    import sys

    from surfaces.interactive_shell.ui import ask_user

    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(ask_user, "menu_columns", lambda: 80)
    question = AskUserQuestion(
        label="Extras",
        title="Which extras?",
        options=("Unit tests", "Dockerfile"),
        multi_select=True,
    )
    ask_user._draw_ask_user(
        questions=(question, question),
        current=0,
        answers=[None, None],
        option_index=0,
        erase_lines=0,
        checked={0},
    )
    plain = re.sub(r"\x1b\[[0-9;:]*[A-Za-z]", "", out.getvalue())
    assert "[x] (A) Unit tests" in plain
    assert "[ ] (B) Dockerfile" in plain
    assert "Space/Enter/A-C Toggle" in plain
    assert "1. Unit tests" not in plain
