"""Core-session invariants: the ``SessionCore`` field surface and its owned behavior.

Pins that ``SessionCore`` carries exactly the surface-agnostic fields (no shell facets),
that ``task_registry`` stays core, and the integration warm-cache generation guard and
token accounting. Shell ``Session``/facet invariants live in
``tests/interactive_shell/session/test_session_facets.py``.
"""

from __future__ import annotations

import dataclasses

from core.agent_harness.accounting.token_usage import TokenUsage
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.session.session_core import SessionCore

# Every surface-agnostic field on SessionCore. The 7 former integration fields collapsed
# into one composed ``integrations`` (IntegrationState); its public fields are properties.
_CORE_FIELDS = (
    "session_id",
    "started_at",
    "store",
    "resumed_from_name",
    "history",
    "last_assistant_intent",
    "pending_schedule_offer",
    "pending_user_choice",
    "ask_user_rounds",
    "active_skill",
    "active_skill_tools",
    "task_plan",
    "task_plan_work",
    "task_plan_work_step_texts",
    "task_plan_breakdown_emitted",
    "plan_only_until_authorized",
    "pending_recovery_note",
    # Outer multi-turn goal, plus the evidence-tier upgrade CTA it can offer.
    "session_goal",
    "pending_integration_setup_offer",
    "offered_upgrade_ctas",
    # SessionGoal gather carry: tools/sources that failed at transport level.
    "gather_unreachable_tools",
    "gather_unreachable_sources",
    "integrations",
    "available_capabilities",
    "accumulated_context",
    "reasoning_effort",
    "tokens",
    "task_registry",
    "agent",
    "grounding",
    "runtime_metadata",
)


def _session() -> SessionCore:
    return SessionCore(store=InMemorySessionStore())


def test_session_core_carries_exactly_the_core_fields_and_no_facets() -> None:
    field_names = {f.name for f in dataclasses.fields(SessionCore)}
    assert field_names == set(_CORE_FIELDS)
    assert "terminal" not in field_names
    assert "alerts" not in field_names
    assert not hasattr(SessionCore(), "terminal")
    assert not hasattr(SessionCore(), "alerts")


def test_task_registry_is_a_core_field() -> None:
    assert "task_registry" in {f.name for f in dataclasses.fields(SessionCore)}


# --------------------------------------------------------------------------- #
# Integration warm-cache generation guard                                     #
# --------------------------------------------------------------------------- #


class TestWarmCacheGeneration:
    def test_stale_generation_is_ignored(self) -> None:
        session = _session()
        session.integrations._warm_generation = 5
        session.integrations._store({"datadog": {"connection_verified": True}}, generation=3)
        assert session.resolved_integrations_cache is None

    def test_empty_resolve_is_not_cached(self) -> None:
        session = _session()
        session.integrations._store({}, generation=session.integrations._warm_generation)
        assert session.resolved_integrations_cache is None

    def test_current_generation_stores_the_cache(self) -> None:
        session = _session()
        gen = session.integrations._warm_generation
        session.integrations._store({"datadog": {"connection_verified": True}}, generation=gen)
        assert session.resolved_integrations_cache is not None
        assert "datadog" in session.resolved_integrations_cache


# --------------------------------------------------------------------------- #
# Token accounting (the /cost totals)                                         #
# --------------------------------------------------------------------------- #


class TestTokenAccounting:
    def test_record_accumulates_totals_and_call_count(self) -> None:
        tokens = TokenUsage()
        tokens.record(input_tokens=10, output_tokens=5)
        tokens.record(input_tokens=3, output_tokens=0)
        assert tokens.totals["input"] == 13
        assert tokens.totals["output"] == 5
        assert tokens.call_count == 2

    def test_zero_record_is_a_no_op(self) -> None:
        tokens = TokenUsage()
        tokens.record(input_tokens=0, output_tokens=0)
        assert tokens.call_count == 0
        assert tokens.totals == {}

    def test_estimated_and_measured_are_bucketed_separately(self) -> None:
        tokens = TokenUsage()
        tokens.record(input_tokens=4, output_tokens=0, estimated=True)
        tokens.record(input_tokens=6, output_tokens=0, estimated=False)
        assert tokens.totals["input_estimated"] == 4
        assert tokens.totals["input_measured"] == 6
        assert tokens.totals["input"] == 10

    def test_reset_clears_all(self) -> None:
        tokens = TokenUsage()
        tokens.record(input_tokens=10, output_tokens=5)
        tokens.reset()
        assert tokens.totals == {}
        assert tokens.call_count == 0
