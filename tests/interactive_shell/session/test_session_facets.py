"""Shell-session invariants: the ``Session`` subclass and its terminal/alerts facets.

Pins that the shell ``Session`` composes ``SessionCore`` plus the ``terminal`` and
``alerts`` facets, that each relocated field cluster lives on its facet, and that the
facet delegation (analytics staging, incoming-alert cap) behaves. Core-only invariants
live in ``tests/core/agent_harness/session/test_session_characterization.py``.
"""

from __future__ import annotations

import dataclasses

from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.session.session_core import SessionCore
from core.domain.alerts.inbox import IncomingAlert
from surfaces.interactive_shell.session.session import Session

# Core fields are inherited from SessionCore; the shell adds these two facets.
# Includes pending_schedule_offer (structured yes → /cron confirmations),
# pending_user_choice (structured decision → /choose selection menu), and
# pending_recovery_note (WAL recovery note for the first turn after /resume),
# plus the session-goal trio (session_goal, pending_integration_setup_offer,
# offered_upgrade_ctas), and the host-owned task-plan work log
# (task_plan_work, task_plan_work_step_texts, task_plan_breakdown_emitted).
_CORE_FIELD_COUNT = 31
_FACET_FIELDS = ("alerts", "terminal")


def _session() -> Session:
    return Session(store=InMemorySessionStore())


def test_session_is_a_session_core_with_two_facets() -> None:
    assert issubclass(Session, SessionCore)
    field_names = {f.name for f in dataclasses.fields(Session)}
    assert "terminal" in field_names
    assert "alerts" in field_names
    assert len(field_names) == _CORE_FIELD_COUNT + len(_FACET_FIELDS)


def test_alert_inbox_facet_holds_the_relocated_alert_state() -> None:
    inbox = _session().alerts
    assert hasattr(inbox, "entries")  # was Session.incoming_alerts
    assert hasattr(inbox, "_max")  # was Session._INCOMING_ALERTS_MAX


def test_terminal_facet_holds_the_theme_cluster() -> None:
    terminal = _session().terminal
    for f in ("active_theme_name", "pending_theme_refresh", "trust_mode"):
        assert hasattr(terminal, f)


def test_terminal_facet_holds_the_prompt_toolkit_cluster() -> None:
    terminal = _session().terminal
    for f in (
        "prompt_history_backend",
        "prompt_app",
        "main_loop",
        "prompt_refresh_fn",
        "fleet_sampler_starter",
    ):
        assert hasattr(terminal, f)


def test_terminal_facet_holds_the_pending_prompt_cluster() -> None:
    terminal = _session().terminal
    for f in (
        "pending_prompt_default",
        "pending_prompt_autosubmit",
        "exclusive_stdin_active",
        "dispatch_active",
    ):
        assert hasattr(terminal, f)


def test_terminal_facet_holds_the_metrics_cluster() -> None:
    terminal = _session().terminal
    for f in ("metrics", "history_generation"):
        assert hasattr(terminal, f)


def test_terminal_facet_holds_the_analytics_staging_cluster() -> None:
    terminal = _session().terminal
    for f in ("_turn_outcome_hint", "_pending_turn_llm", "_pending_turn_error"):
        assert hasattr(terminal, f)


def test_analytics_staging_pop_methods_consume_exactly_once() -> None:
    terminal = _session().terminal
    terminal.set_turn_outcome_hint("handled")
    assert terminal.pop_turn_outcome_hint() == "handled"
    assert terminal.pop_turn_outcome_hint() is None
    terminal.set_turn_outcome_hint("   ")
    assert terminal.pop_turn_outcome_hint() is None


def test_incoming_alerts_are_capped_and_drop_oldest_first() -> None:
    session = _session()
    cap = session.alerts._max
    for i in range(cap + 5):
        session.record_incoming_alert(IncomingAlert(text=f"alert-{i}"))
    assert len(session.alerts.entries) == cap
    assert session.alerts.entries[0].text == "alert-5"
    assert session.alerts.entries[-1].text == f"alert-{cap + 4}"
