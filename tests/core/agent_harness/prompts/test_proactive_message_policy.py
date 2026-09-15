"""Contracts for the bundled proactive-message master policy."""

from __future__ import annotations

from core.agent_harness import load_master_judgement


def test_master_judgement_is_event_driven_and_bounded() -> None:
    policy = load_master_judgement()

    assert policy.enabled is True
    assert policy.trigger == "after_slack_turn"
    assert policy.schedule == "event_driven"
    assert policy.rule_names == ("ci_blocker", "threshold_event", "integration_health")
    assert policy.slack_context_enabled is True
    assert policy.slack_context_limit == 20


def test_master_judgement_requires_verified_actionable_novelty() -> None:
    policy = load_master_judgement()
    collapsed = " ".join(policy.body.split()).lower()

    assert "new, verified information" in collapsed
    assert "exact evidence quote" in collapsed
    assert "clear owner" in collapsed
    assert "timing can materially affect" in collapsed
    assert "originating thread" in collapsed
    assert "continuous slack polling" in collapsed
