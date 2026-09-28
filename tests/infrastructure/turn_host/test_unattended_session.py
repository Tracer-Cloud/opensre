"""Answering a parked choice on an unattended session, and approvals as questions."""

from __future__ import annotations

import json

import pytest

from core.agent_harness import SessionCore
from core.agent_harness.session.pending_choice import PendingUserChoice
from core.agent_harness.spi.handoff import AskUserQuestion, question_key
from infrastructure.turn_host.unattended_session import (
    AnswerRejected,
    answer_pending_choice,
    approval_grant,
    approval_question,
    choice_view,
    invocation_key,
)


def test_a_number_or_label_selects_an_option_and_free_text_needs_permission() -> None:
    # Arrange
    strict = SessionCore()
    strict.pending_user_choice = PendingUserChoice(
        title="Which branch?", options=("main", "release"), custom_answer=False
    )
    lenient = SessionCore()
    lenient.pending_user_choice = PendingUserChoice(title="Which branch?", options=("main",))

    # Act
    by_number = answer_pending_choice(strict, "2")
    strict.pending_user_choice = PendingUserChoice(
        title="Which branch?", options=("main", "release"), custom_answer=False
    )
    with pytest.raises(AnswerRejected):
        answer_pending_choice(strict, "develop")
    free_text = answer_pending_choice(lenient, "develop")

    # Assert: answers arrive as the next user message and the question is settled
    assert by_number.startswith("1. Which branch?") and '"release"' in by_number
    assert '"develop"' in free_text
    assert lenient.pending_user_choice is None
    assert question_key("Which branch?") in lenient.questions_already_answered


def test_several_questions_take_a_json_object_keyed_by_title_or_position() -> None:
    # Arrange
    session = SessionCore()
    session.pending_user_choice = PendingUserChoice(
        title="Setup",
        options=("a", "b"),
        questions=(
            AskUserQuestion(label="scope", title="Scope?", options=("a", "b")),
            AskUserQuestion(label="window", title="Window?", options=("1h", "24h")),
        ),
    )

    strict = SessionCore()
    strict.pending_user_choice = PendingUserChoice(
        title="Setup",
        options=("a", "b"),
        questions=(AskUserQuestion(label="s", title="Scope?", options=("a", "b")),) * 2,
        custom_answer=False,
    )

    # Act
    text = answer_pending_choice(session, json.dumps({"Scope?": "b", "2": "24h"}))
    with pytest.raises(AnswerRejected):
        answer_pending_choice(strict, json.dumps({"1": "zzz", "2": "a"}))

    # Assert: positions and titles both address a question; free text obeys the choice's policy
    assert '"b"' in text and '"24h"' in text and "2. Window?" in text


def test_an_approval_question_grants_exactly_the_previewed_call_and_only_on_approve() -> None:
    # Arrange
    arguments = {"repo": "r", "pr_number": 7}
    pending = approval_question(
        "schedule_ci_repair_loop", arguments, "Starts a worker.", '{"repo": "r"}'
    )
    plain = PendingUserChoice(title="Which branch?", options=("main",))

    # Act
    granted = approval_grant(pending, "approve")
    view = choice_view(pending)

    # Assert: the grant names the tool and its exact arguments; the details reach the caller
    assert granted == invocation_key("schedule_ci_repair_loop", arguments)
    assert granted != invocation_key("schedule_ci_repair_loop", {"repo": "r", "pr_number": 8})
    assert pending.options == ("Approve", "Deny") and pending.custom_answer is False
    assert view["note"] == 'Starts a worker.\n{"repo": "r"}'
    assert approval_grant(pending, "Deny") is None
    assert approval_grant(plain, "Approve") is None


def test_restating_a_call_with_its_defaults_spelled_out_is_the_same_call() -> None:
    """Absent, null and schema-default arguments do not change a call's approval key."""
    # Arrange
    schema = {
        "type": "object",
        "properties": {
            "demo": {"type": "boolean", "default": False},
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "pr_number": {"type": "integer"},
        },
    }
    spelled_out = {"demo": False, "owner": "o", "repo": "r", "pr_number": 7}
    restated = {"owner": "o", "repo": "r", "pr_number": 7, "branch": None}

    # Act
    approved = invocation_key("schedule_ci_repair_loop", spelled_out, schema=schema)
    again = invocation_key("schedule_ci_repair_loop", restated, schema=schema)
    changed = invocation_key(
        "schedule_ci_repair_loop", {**spelled_out, "demo": True}, schema=schema
    )
    unaware = invocation_key("schedule_ci_repair_loop", spelled_out)

    # Assert: defaults and absent values do not change the call; a real change does
    assert approved == again
    assert approved != changed
    assert unaware != approved
