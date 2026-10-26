"""Goal contract — setup-state facts stay guidance-free.

Schedule-capacity copy lives on the Python constant, not in the shared
system-prompt markdown.
"""

from __future__ import annotations

import re

from core.agent_harness.prompts.action.text import (
    ACTION_SETUP_CAPACITY_SCHEDULE_RULE,
)


def _mentions(text: str, *needles: str) -> None:
    lower = text.lower()
    missing = [n for n in needles if n.lower() not in lower]
    assert not missing, f"missing {missing!r} in:\n{text}"


def test_action_capacity_rule_ties_facts_to_propose_not_skip() -> None:
    # Arrange / Act
    rule = ACTION_SETUP_CAPACITY_SCHEDULE_RULE

    # Assert: read facts → propose when capacity; empty integrations → no invent
    _mentions(
        rule,
        "setup-state",
        "Integrations connected",
        "none",
        "propose_scheduled_delivery",
        "WAIT",
        "schedule_count",
        "/integrations setup",
    )
    assert "do not invent" in rule.lower() or "not invent" in rule.lower()
    # Must not tell the planner to skip offers merely because schedules exist.
    assert re.search(r"do not skip the offer only because schedule_count", rule, re.I)
    assert len(rule) < 600


def test_goal_contract_does_not_belong_in_setup_state_facts() -> None:
    # Arrange: render_setup_state must stay guidance-free (plan invariant).
    from infrastructure.setup_state import SetupSnapshot, render_setup_state

    # Act
    facts = render_setup_state(
        SetupSnapshot(
            integrations=("slack",), schedule_count=0, deliverable_count=0, last_delivery_ok=None
        )
    )

    # Assert
    assert "propose_scheduled_delivery" not in facts
    assert "do not nag" not in facts.lower()
    assert "Integrations connected: slack" in facts
