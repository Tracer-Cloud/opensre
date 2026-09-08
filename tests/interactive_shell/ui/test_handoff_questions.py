"""Human hand-off questions vs answers are styled differently."""

from __future__ import annotations

import io

from rich.console import Console

from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.handoff_questions import (
    render_ask_user_qa,
    render_choice_selection,
    try_render_ask_user_submission,
)
from surfaces.interactive_shell.ui.input_prompt.rendering import render_submitted_prompt
from surfaces.interactive_shell.ui.streaming.renderer import render_markdown_block


def test_render_markdown_block_highlights_a_question() -> None:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    render_markdown_block(console, "Which environment should I investigate first?")
    output = buffer.getvalue()
    assert "?" in output
    assert "Which environment should I investigate first?" in output


def test_submitted_handoff_answer_is_the_user_row() -> None:
    session = Session()
    # Only a structured picker / Ask-User handoff sets this flag; a plain
    # assistant question in prose must not trigger the answer treatment.
    session.terminal.awaiting_handoff_answer = True
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    render_submitted_prompt(console, session, "staging")
    output = buffer.getvalue()
    # No hanging ``↗ answer`` — the user row is the answer (Droid / Cursor).
    assert "↗ answer" not in output
    assert "staging" in output


def test_ask_user_answers_render_as_numbered_qa() -> None:
    session = Session()
    session.terminal.awaiting_handoff_answer = True
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    text = (
        "1. Where does the /api/orders service live?\n"
        "Hypothetical/demo scenario, no real code\n"
        "\n"
        "2. What's the time window of the p99 regression?\n"
        "Last 7 days"
    )
    render_submitted_prompt(console, session, text)
    output = buffer.getvalue()
    assert "Ask User" in output
    assert "↗ You answered" not in output
    assert "Where does the /api/orders service live?" in output
    assert "Hypothetical/demo scenario, no real code" in output
    assert "Last 7 days" in output
    assert session.terminal.submitted_turn_count == 1


def test_ask_user_qa_highlights_answer_differently_from_question() -> None:
    # 10.6 core contract: the answer must render in a distinct colour from the
    # question, under a highlighted "Ask User" header, so a filled-in recap reads
    # apart at a glance. Pin the colours, not just the text.
    from infrastructure.terminal import theme as ui_theme

    buffer = io.StringIO()
    console = Console(
        file=buffer,
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        no_color=False,
    )
    render_ask_user_qa(console, [("Which product should I demo?", "OpenSRE itself")])
    raw = buffer.getvalue()

    def _sgr(hex_color: str) -> str:
        r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
        return f"38;2;{r};{g};{b}"

    question_sgr = _sgr(str(ui_theme.TEXT))
    answer_sgr = _sgr(str(ui_theme.BRAND))
    header_sgr = _sgr(str(ui_theme.HIGHLIGHT))
    assert question_sgr != answer_sgr  # the two colours genuinely differ
    assert header_sgr in raw  # "Ask User" header in the accent colour
    assert f"1;{question_sgr}" in raw  # question is bold TEXT (droid-style emphasis)
    assert answer_sgr in raw  # answer highlighted in BRAND, distinct from the question


def test_ask_user_qa_leaves_blank_rows_between_pairs() -> None:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    render_ask_user_qa(
        console,
        [
            ("How large is the p99 regression on /api/orders?", "Percentage increase only"),
            ("When did the regression begin?", "Sudden without known change"),
        ],
    )
    lines = [line.rstrip() for line in buffer.getvalue().splitlines()]
    header = next(index for index, line in enumerate(lines) if line.strip() == "Ask User")
    assert lines[header + 1] == ""
    first_answer = next(
        index for index, line in enumerate(lines) if "Percentage increase only" in line
    )
    assert lines[first_answer + 1] == ""
    assert "When did the regression begin?" in lines[first_answer + 2]


def test_choose_slash_is_not_echoed() -> None:
    session = Session()
    session.terminal.awaiting_handoff_answer = True
    session.terminal.last_input_autosubmitted = True
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    render_submitted_prompt(console, session, "/choose")
    assert session.terminal.awaiting_handoff_answer is True
    assert buffer.getvalue() == ""
    assert session.terminal.submitted_turn_count == 0
    # The queued /choose must not leave a stale autosubmit flag, or the next
    # genuine turn is misread as autosubmitted and skips the round-counter reset.
    assert session.terminal.last_input_autosubmitted is False


def test_auto_submitted_single_choice_is_not_echoed_as_a_user_turn() -> None:
    session = Session()
    session.terminal.awaiting_handoff_answer = True
    session.terminal.last_input_autosubmitted = True
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)

    render_submitted_prompt(console, session, "Blue-green")

    assert buffer.getvalue() == ""
    assert session.terminal.submitted_turn_count == 0
    assert session.terminal.pending_choice_response == "Blue-green"


def test_single_answer_with_its_question_marks_only_the_label_as_the_choice() -> None:
    # Arrange: a single-menu answer arrives as "1. question\nlabel", auto-submitted.
    from core.agent_harness.session.pending_choice import AskUserQuestion, format_ask_user_answers
    from surfaces.interactive_shell.session import Session
    from surfaces.interactive_shell.ui.input_prompt.rendering import render_submitted_prompt

    session = Session()
    session.terminal.awaiting_handoff_answer = True
    session.terminal.last_input_autosubmitted = True
    question = AskUserQuestion(label="", title="Which repository should I analyze?", options=("a",))
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)

    # Act
    render_submitted_prompt(console, session, format_ask_user_answers((question,), ("acme/app",)))

    # Assert: no second user row, and the acknowledgement filter sees the label alone.
    assert buffer.getvalue() == ""
    assert session.terminal.pending_choice_response == "acme/app"


def test_choice_selection_strips_terminal_controls() -> None:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)

    render_choice_selection(console, "Deploy?\x1b]0;pwn\x07", "Canary\x1b[2K")

    output = buffer.getvalue()
    assert "\x1b" not in output
    assert "\x07" not in output
    # One answered line, not the question asked again under a new header.
    assert "Ask User" not in output
    assert "Deploy?" in output and "Canary" in output
    assert output.strip().count("\n") == 0
    assert "✓" not in output
    # Section gap above the line so it does not join Plan complete.
    assert output.startswith("\n")


def test_multi_select_choice_indents_every_selected_line() -> None:
    # Arrange: a multi-select answer arrives as one option per line.
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=100)
    answer = "Audit the architecture\nFind failing PRs\nRemediate alerts"

    # Act
    render_choice_selection(console, "Select Complex Demos", answer)

    # Assert: the question once, then every option indented under it.
    lines = [line.rstrip() for line in buffer.getvalue().splitlines() if line.strip()]
    assert lines[0].endswith("Select Complex Demos")
    for label in ("Audit the architecture", "Find failing PRs", "Remediate alerts"):
        assert any(label in line and line.startswith(" ") for line in lines)
        assert label not in lines
    assert "✓" not in buffer.getvalue()


def test_choice_selection_is_not_a_plan_step() -> None:
    """Single-pick recap must not look like another Plan complete checklist row."""
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)

    render_choice_selection(
        console,
        "Choose a Demo",
        "Explore a repo and analyze its CI/CD performance (recommended)",
    )

    output = buffer.getvalue()
    assert "Ask User" not in output
    assert "Choose a Demo" in output
    assert "Explore a repo" in output
    assert "✓ Choose a Demo" not in output
    assert "✓" not in output


def test_try_render_rejects_a_single_choice_label() -> None:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    assert try_render_ask_user_submission(console, "Commit the changes") is False
    assert buffer.getvalue() == ""


def test_plain_assistant_question_does_not_tag_the_next_turn() -> None:
    """A conversational opener ending in ``?`` must not paint the follow-up.

    Without the ``awaiting_handoff_answer`` flag the turn is an ordinary user
    turn: no ``↗ answer`` marker and the input keeps the neutral row treatment.
    """
    session = Session()
    session.cli_agent_messages = [
        ("user", "good evening"),
        ("assistant", "Good evening! How can I help?"),
    ]
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    render_submitted_prompt(console, session, "how many open PRs in opensre?")
    output = buffer.getvalue()
    assert "↗ answer" not in output
    assert "how many open PRs in opensre?" in output
