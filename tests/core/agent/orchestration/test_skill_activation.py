"""Skill context survives answers and slash commands, but not unrelated requests."""

from types import SimpleNamespace

import pytest

from core.agent_harness.session.pending_choice import AskUserQuestion, format_ask_user_answers
from core.agent_harness.turns.skill_activation import prepare_active_skill


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Inspect a deployment", None),
        ("/auto high", "analyzing-github-ci-performance"),
        (
            format_ask_user_answers(
                (AskUserQuestion(label="", title="Repository?", options=("acme/one",)),),
                ("acme/one",),
            ),
            "analyzing-github-ci-performance",
        ),
    ],
)
def test_request_boundaries_preserve_only_the_current_flow(
    message: str, expected: str | None
) -> None:
    session = SimpleNamespace(active_skill="analyzing-github-ci-performance")
    prepare_active_skill(session, message)
    assert session.active_skill == expected
