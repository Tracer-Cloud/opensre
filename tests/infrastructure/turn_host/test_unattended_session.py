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
    approval_question,
    approved_tool,
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

    # Act
    text = answer_pending_choice(session, json.dumps({"Scope?": "b", "2": "24h"}))

    # Assert
    assert '"b"' in text and '"24h"' in text and "2. Window?" in text


def test_an_approval_question_grants_the_tool_only_on_approve() -> None:
    # Arrange
    pending = approval_question("schedule_ci_repair_loop", "Starts a worker.", '{"repo": "r"}')
    plain = PendingUserChoice(title="Which branch?", options=("main",))

    # Act / Assert
    assert pending.options == ("Approve", "Deny") and pending.custom_answer is False
    assert "Starts a worker." in pending.note and '{"repo": "r"}' in pending.note
    assert approved_tool(pending, "approve") == "schedule_ci_repair_loop"
    assert approved_tool(pending, "Deny") is None
    assert approved_tool(plain, "Approve") is None
