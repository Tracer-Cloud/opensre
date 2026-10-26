"""Replay fidelity for ``/resume``: the transcript, not the bookkeeping log.

Session history is append-only *bookkeeping*: one slash command writes a
``slash`` stub, a ``cli_agent`` stub, and an analytics payload as its
"response". Replaying those rows verbatim showed each command three times, the
last of them in the assistant gutter as if the model had said it. A menu
answer has the same problem in the other direction: it is stored in the
``@json:`` framing its parser needs, never in the form the user saw.
"""

from __future__ import annotations

import io

from rich.console import Console

from core.agent_harness.spi.handoff import AskUserQuestion, format_ask_user_answers
from surfaces.interactive_shell.command_registry.session_cmds.resume_rendering import (
    render_resumed_session_history,
)
from surfaces.interactive_shell.ui.transcript_view.store import _RENDER_HEIGHT


def _render(history: list[dict], turn_details: list[dict], *, width: int = 78) -> str:
    buffer = io.StringIO()
    render_resumed_session_history(
        Console(
            file=buffer, force_terminal=False, highlight=False, height=_RENDER_HEIGHT, width=width
        ),
        history=history,
        turn_details=turn_details,
        messages=[],
    )
    return buffer.getvalue()


def _render_raw(history: list[dict], turn_details: list[dict], *, width: int) -> str:
    """Like :func:`_render` but keeping ANSI, for comparing against the live echo."""
    buffer = io.StringIO()
    render_resumed_session_history(
        Console(
            file=buffer,
            force_terminal=True,
            color_system="truecolor",
            highlight=False,
            no_color=False,
            height=_RENDER_HEIGHT,
            width=width,
        ),
        history=history,
        turn_details=turn_details,
        messages=[],
    )
    return buffer.getvalue()


def _slash_rows(text: str) -> list[dict]:
    """The two history rows one dispatched slash command actually writes."""
    return [
        {"kind": "slash", "text": text, "timestamp": "2026-10-07T09:00:00+00:00"},
        {"kind": "cli_agent", "text": text, "timestamp": "2026-10-07T09:00:01+00:00"},
    ]


def test_slash_turn_replays_once_and_never_shows_its_analytics_payload() -> None:
    """The headline defect: ``$ /version`` + ``❱ /version`` + ``● slash … (succeeded)``.

    A failure keeps its message, because that is the part the reader can act on;
    a success with nothing to add stays silent rather than echoing the command.
    """
    output = _render(
        _slash_rows("/version") + _slash_rows("/model set"),
        [
            {"prompt": "/version", "response": "slash /version (succeeded)"},
            {
                "prompt": "/model set",
                "response": "slash /model set (failed)\nRun /logout first.",
            },
        ],
    )

    assert output.count("/version") == 1
    assert output.count("/model set") == 1
    assert "(succeeded)" not in output
    assert "(failed)" not in output
    assert "● slash" not in output
    assert "✗ Run /logout first." in output


def test_internal_choose_turn_is_not_replayed() -> None:
    """``/choose`` drives an exclusive-stdin picker; the live echo never paints it."""
    output = _render(_slash_rows("/choose") + _slash_rows("/version"), [])

    assert "/choose" not in output
    assert "❱ /version" in output


def test_a_handlers_own_outcome_prose_survives_the_replay() -> None:
    """An ``outcome_hint`` is user-facing text, not a parseable analytics payload.

    It is shown verbatim and without a verdict: the hint carries no status and
    the persisted row's ``ok`` is not restored, so neither tick nor cross can be
    justified from what the replay can see.
    """
    output = _render(
        _slash_rows("/auto"),
        [{"prompt": "/auto", "response": "auto-approve: high"}],
    )

    assert "auto-approve: high" in output
    assert "✓" not in output


def test_replayed_rows_are_laid_out_at_the_render_width() -> None:
    """Replay goes through the same renderables as a live turn, so the full-screen
    transcript re-lays it out on resize instead of keeping fixed-width bytes."""
    history = [
        {"kind": "cli_agent", "text": "why did the deploy to prod-us-east fail at 03:12?"},
    ]
    details = [
        {
            "prompt": "why did the deploy to prod-us-east fail at 03:12?",
            "response": "Four of nine pods never pulled the image tag.",
        }
    ]
    for width in (78, 44, 26):
        for row in (row for row in _render(history, details, width=width).splitlines() if row):
            assert len(row) <= width, (width, len(row), row)


def test_a_replayed_turn_renders_exactly_like_a_live_one() -> None:
    """The point of sharing the renderer: a conversation must not change
    appearance just because it was restored. Pins marker, plate and spacing
    against the live echo rather than against a copy of its output."""
    from surfaces.interactive_shell.session import Session
    from surfaces.interactive_shell.ui.input_prompt.rendering import render_submitted_prompt

    prompt = "why is redis slow?"
    live = io.StringIO()
    render_submitted_prompt(
        Console(
            file=live,
            force_terminal=True,
            color_system="truecolor",
            highlight=False,
            no_color=False,
            height=_RENDER_HEIGHT,
            width=64,
        ),
        Session(),
        prompt,
    )
    replayed = _render_raw([{"kind": "cli_agent", "text": prompt}], [], width=64)

    assert live.getvalue().strip() in replayed.strip()


def test_two_identical_submissions_replay_as_two_turns() -> None:
    """Only the bookkeeping pair of one submission collapses.

    Regression: the fold matched any neighbouring row with the same text, so
    running `/model set` twice replayed once and the banner undercounted.
    """
    from surfaces.interactive_shell.command_registry.session_cmds.resume_rendering import (
        replayable_turn_count,
    )

    history = _slash_rows("/model set") + _slash_rows("/model set")

    assert replayable_turn_count(history) == 2
    assert _render(history, []).count("/model set") == 2


def test_two_identical_chat_turns_both_replay() -> None:
    """A repeated question is two turns; only slash rows come in pairs."""
    history = [
        {"kind": "cli_agent", "text": "retry the deploy"},
        {"kind": "cli_agent", "text": "retry the deploy"},
    ]

    output = _render(
        history,
        [{"prompt": "retry the deploy", "response": "Redeploying."}],
    )

    assert output.count("❱ retry the deploy") == 2


def test_an_unreadable_outcome_is_not_labelled_a_success() -> None:
    """`format_wizard_cli_outcome` emits prose with no status marker, and the
    persisted row's `ok` is not restored — so the result cannot be known here.

    Regression: unrecognised payloads defaulted to `ok`, putting a success tick
    on `interactive wizard failed (exit 2)`.
    """
    output = _render(
        _slash_rows("/onboard"),
        [
            {
                "prompt": "/onboard",
                "response": "opensre onboard: interactive wizard failed (exit 2)",
            }
        ],
    )

    assert "interactive wizard failed (exit 2)" in output
    assert "✓" not in output
    assert "✗" not in output


def test_session_navigation_is_not_replayed_as_conversation() -> None:
    """A session's file records the ``/resume`` that opened it, under two texts:
    the handler writes ``/resume <id>`` and turn accounting writes ``/resume``.
    Replaying them showed the command twice at the top of its own conversation.
    """
    output = _render(
        [
            {"kind": "slash", "text": "/resume 55ff6dcb"},
            {"kind": "cli_agent", "text": "/resume"},
            {"kind": "slash", "text": "/sessions"},
            {"kind": "cli_agent", "text": "/sessions"},
            {"kind": "cli_agent", "text": "why is redis slow?"},
        ],
        [
            {"prompt": "/resume", "response": "terminal turn handled: /resume"},
            {"prompt": "why is redis slow?", "response": "Connection pool exhaustion."},
        ],
    )

    assert "/resume" not in output
    assert "/sessions" not in output
    assert "terminal turn handled" not in output
    assert "❱ why is redis slow?" in output


def test_a_slash_turn_is_recognised_by_its_text_not_its_bookkeeping_row() -> None:
    """Only one of the two stubs a dispatched slash writes may reach a branch.
    Reading the kind alone replayed the survivor as prose in the ``●`` gutter."""
    output = _render(
        [{"kind": "cli_agent", "text": "/model set"}],
        [{"prompt": "/model set", "response": "slash /model set (failed)\nRun /logout."}],
    )

    assert "✗ Run /logout." in output
    assert "●" not in output


def test_the_banner_counts_only_the_turns_that_replay() -> None:
    """Counting raw history rows promised turns the reader never sees."""
    from surfaces.interactive_shell.command_registry.session_cmds.resume_rendering import (
        replayable_turn_count,
    )

    history = [
        {"kind": "slash", "text": "/choose"},
        {"kind": "cli_agent", "text": "/choose"},
        {"kind": "slash", "text": "/resume 55ff6dcb"},
        {"kind": "cli_agent", "text": "/resume"},
        {"kind": "slash", "text": "/auto high"},
        {"kind": "cli_agent", "text": "/auto high"},
        {"kind": "cli_agent", "text": "why is redis slow?"},
    ]

    assert replayable_turn_count(history) == 2


def test_a_menu_answer_replays_as_its_card_not_as_the_json_the_parser_reads() -> None:
    """``@json:`` framing keeps a custom answer from impersonating a question header.

    It is wire format: live, the user picked from a menu that erased itself and
    scrollback kept an Ask User card. Replaying the stored text as a prompt
    plate put the framing on screen instead.
    """
    answer = format_ask_user_answers(
        (
            AskUserQuestion(label="", title="Which repository should I analyze?", options=()),
            AskUserQuestion(label="", title="How far back?", options=()),
        ),
        ("acme/app", "30 days"),
    )

    output = _render([{"kind": "cli_agent", "text": answer}], [])

    assert "@json:" not in output
    assert "Ask User" in output
    assert "Which repository should I analyze?" in output
    assert "acme/app" in output
    assert "How far back?" in output
    assert "30 days" in output


def test_a_multiline_numbered_request_is_not_mistaken_for_a_menu_answer() -> None:
    """The replay has no hand-off provenance, so only the ``@json:`` framing counts.

    ``parse_ask_user_answers`` also accepts the legacy unframed format, which a
    numbered request over two lines satisfies by accident; the live renderer can
    afford that because it gates on ``awaiting_handoff_answer`` first. Reading
    the text alone, as the replay must, that leniency turns a typed request into
    somebody else's question.
    """
    output = _render(
        [{"kind": "cli_agent", "text": "1. Investigate the outage\nCheck the logs"}], []
    )

    assert "Ask User" not in output
    assert "Investigate the outage" in output
    assert "Check the logs" in output
