"""Tenancy and concurrency invariants for proactive-message persistence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from infrastructure.proactive_messages import DecisionLedger, ProactiveMessageDecision


def _decision() -> ProactiveMessageDecision:
    return ProactiveMessageDecision(
        decision="suppress",
        rationale="No material timing.",
    )


def _record(scope: StorageScope) -> bool:
    with bound_storage_scope(scope):
        _record, created = DecisionLedger().record_decision(
            session_id="session",
            interaction_id="interaction",
            end_record_id="end",
            policy_name="master-judgement",
            policy_version=1,
            decision=_decision(),
            signal_fingerprint="",
            channel_id="C12345678",
            thread_ts="100.1",
        )
        return created


def test_concurrent_judges_create_only_one_interaction_decision(scope: StorageScope) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        created = list(pool.map(lambda _index: _record(scope), range(24)))

    with bound_storage_scope(scope):
        decisions = DecisionLedger().recent(50)

    assert created.count(True) == 1
    assert len(decisions) == 1


def test_actor_ledgers_are_isolated_within_one_org(scope: StorageScope) -> None:
    other = StorageScope(principal=Principal.org("org_proactive"), actor=Actor("U_OTHER"))
    assert _record(scope) is True

    with bound_storage_scope(other):
        assert DecisionLedger().recent() == []
