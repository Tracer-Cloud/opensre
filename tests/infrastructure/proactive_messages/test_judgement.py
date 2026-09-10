"""Proactive judgement sends only grounded, novel, actionable signals."""

from __future__ import annotations

import hashlib
from typing import Any

from config.principal import StorageScope
from config.scope_context import bound_storage_scope
from infrastructure.proactive_messages import (
    DecisionLedger,
    JudgementCursor,
    ProactiveJudgementRunner,
    ProactiveMessageDecision,
)
from tests.infrastructure.proactive_messages.conftest import write_interaction


class _StructuredLLM:
    def __init__(self, decision: ProactiveMessageDecision) -> None:
        self._decision = decision
        self.prompts: list[str] = []

    def with_structured_output(self, _model: object) -> _StructuredLLM:
        return self

    def invoke(self, prompt: str) -> ProactiveMessageDecision:
        self.prompts.append(prompt)
        return self._decision


class _FailingStructuredLLM:
    def with_structured_output(self, _model: object) -> _FailingStructuredLLM:
        return self

    def invoke(self, _prompt: str) -> ProactiveMessageDecision:
        raise RuntimeError("provider unavailable")


def _send_decision(
    *, quote: str = "flaky test test_retry failed on attempt 1"
) -> ProactiveMessageDecision:
    return ProactiveMessageDecision(
        decision="send",
        rationale="An unresolved flaky test can break the next merge.",
        message=(
            "CI follow-up: flaky test test_retry failed on attempt 1 in the merged run. "
            "<@U_PROACTIVE>, isolate its shared retry state and rerun it under xdist "
            "before the next merge."
        ),
        signal_key="github-ci:test_retry:flaky",
        signal_state="attempt 1",
        new_verified_information=True,
        clear_owner_and_action=True,
        material_timing=True,
        verified_information="flaky test test_retry failed on attempt 1",
        evidence_quote=quote,
        evidence_source="session",
        owner="U_PROACTIVE",
        next_action="Isolate shared retry state and rerun test_retry under xdist.",
        material_timing_reason="Before the next merge using this CI job.",
    )


def _context_reader(**_kwargs: Any) -> dict[str, Any]:
    return {"status": "read", "messages": [], "message_count": 0}


def test_grounded_send_is_ledgered_before_same_thread_delivery(scope: StorageScope) -> None:
    trigger = write_interaction(scope, session_id="session-send")
    llm = _StructuredLLM(_send_decision())
    deliveries: list[dict[str, str]] = []

    def _deliver(*, channel_id: str, thread_ts: str, message: str) -> str:
        with bound_storage_scope(scope):
            pending = DecisionLedger().recent(1)
        assert pending[0]["delivery_status"] == "pending"
        deliveries.append({"channel_id": channel_id, "thread_ts": thread_ts, "message": message})
        return "200.2"

    runner = ProactiveJudgementRunner(
        context_reader=_context_reader,
        delivery=_deliver,
        llm_factory=lambda: llm,
    )
    with bound_storage_scope(scope):
        outcome = runner.run(trigger)
        decision = DecisionLedger().recent(1)[0]
        cursor = JudgementCursor().last(trigger.session_id)

    assert outcome.status == "delivered"
    assert deliveries == [
        {
            "channel_id": trigger.channel_id,
            "thread_ts": trigger.thread_ts,
            "message": _send_decision().message,
        }
    ]
    assert decision["slack_message_ts"] == "200.2"
    assert decision["delivery_status"] == "delivered"
    assert decision["signal_fingerprint_version"] == 2
    assert cursor is not None and cursor["end_record_id"] == trigger.end_record_id
    assert 'untrusted="true"' in llm.prompts[0]


def test_hallucinated_evidence_is_deterministically_suppressed(scope: StorageScope) -> None:
    trigger = write_interaction(scope, session_id="session-ungrounded")
    deliveries: list[str] = []
    runner = ProactiveJudgementRunner(
        context_reader=_context_reader,
        delivery=lambda **_kwargs: deliveries.append("sent") or "ts",
        llm_factory=lambda: _StructuredLLM(_send_decision(quote="production is down")),
    )

    with bound_storage_scope(scope):
        outcome = runner.run(trigger)
        decision = DecisionLedger().recent(1)[0]

    assert outcome.status == "suppressed"
    assert deliveries == []
    assert decision["decision"] == "suppress"
    assert "could not ground" in decision["rationale"]


def test_grounded_quote_cannot_support_an_unrelated_claim(scope: StorageScope) -> None:
    trigger = write_interaction(scope, session_id="session-unrelated-claim")
    decision = _send_decision(quote="Review the merged CI run")
    runner = ProactiveJudgementRunner(
        context_reader=_context_reader,
        delivery=lambda **_kwargs: "ts",
        llm_factory=lambda: _StructuredLLM(decision),
    )

    with bound_storage_scope(scope):
        outcome = runner.run(trigger)
        recorded = DecisionLedger().recent(1)[0]

    assert outcome.status == "suppressed"
    assert recorded["decision"] == "suppress"
    assert "unsupported by its evidence quote" in recorded["rationale"]


def test_unchanged_signal_is_suppressed_even_when_worded_differently(
    scope: StorageScope,
) -> None:
    first = write_interaction(scope, session_id="session-first", suffix="first")
    second = write_interaction(scope, session_id="session-second", suffix="second")
    sent: list[str] = []
    first_runner = ProactiveJudgementRunner(
        context_reader=_context_reader,
        delivery=lambda **_kwargs: sent.append("sent") or f"ts-{len(sent)}",
        llm_factory=lambda: _StructuredLLM(_send_decision()),
    )
    reworded = _send_decision().model_copy(
        update={
            "message": (
                "CI follow-up: flaky test test_retry failed on attempt 1 in the merged run. "
                "<@U_PROACTIVE>, stabilize its retry fixture before release."
            ),
            "verified_information": (
                "The observed state remains: flaky test test_retry failed on attempt 1."
            ),
            "next_action": "Stabilize the retry fixture.",
            "material_timing_reason": "Before release.",
        }
    )
    second_runner = ProactiveJudgementRunner(
        context_reader=_context_reader,
        delivery=lambda **_kwargs: sent.append("sent") or f"ts-{len(sent)}",
        llm_factory=lambda: _StructuredLLM(reworded),
    )

    with bound_storage_scope(scope):
        first_outcome = first_runner.run(first)
        second_outcome = second_runner.run(second)
        decisions = DecisionLedger().recent(2)

    assert first_outcome.status == "delivered"
    assert second_outcome.status == "suppressed"
    assert sent == ["sent"]
    assert decisions[0]["decision"] == "suppress"
    assert "unchanged recurring signal" in decisions[0]["rationale"]


def test_changed_signal_with_reused_key_is_delivered(scope: StorageScope) -> None:
    first = write_interaction(scope, session_id="session-old-signal", suffix="old")
    second = write_interaction(
        scope,
        session_id="session-new-signal",
        suffix="new",
        assistant="The merged run shows flaky test test_retry failed on attempt 2.",
    )
    sent: list[str] = []

    with bound_storage_scope(scope):
        first_outcome = ProactiveJudgementRunner(
            context_reader=_context_reader,
            delivery=lambda **_kwargs: sent.append("old") or "ts-old",
            llm_factory=lambda: _StructuredLLM(_send_decision()),
        ).run(first)
        changed = _send_decision(quote="flaky test test_retry failed on attempt 2").model_copy(
            update={
                "message": (
                    "CI follow-up: flaky test test_retry failed on attempt 2 in the merged run. "
                    "<@U_PROACTIVE>, isolate its shared retry state and rerun it under xdist "
                    "before the next merge."
                ),
                "signal_state": "attempt 2",
                "verified_information": "flaky test test_retry failed on attempt 2",
            }
        )
        second_outcome = ProactiveJudgementRunner(
            context_reader=_context_reader,
            delivery=lambda **_kwargs: sent.append("new") or "ts-new",
            llm_factory=lambda: _StructuredLLM(changed),
        ).run(second)

    assert first_outcome.status == "delivered"
    assert second_outcome.status == "delivered"
    assert sent == ["old", "new"]


def test_legacy_delivered_signal_is_suppressed_after_fingerprint_upgrade(
    scope: StorageScope,
) -> None:
    legacy_trigger = write_interaction(scope, session_id="session-legacy", suffix="legacy")
    current_trigger = write_interaction(scope, session_id="session-current", suffix="current")
    legacy_decision = _send_decision()
    legacy_material = "|".join(
        (
            legacy_decision.verified_information.casefold(),
            legacy_decision.owner.casefold(),
        )
    )
    legacy_fingerprint = hashlib.sha256(legacy_material.encode("utf-8")).hexdigest()
    with bound_storage_scope(scope):
        record, _created = DecisionLedger().record_decision(
            session_id=legacy_trigger.session_id,
            interaction_id="assistant-legacy",
            end_record_id=legacy_trigger.end_record_id,
            policy_name="master-judgement",
            policy_version=1,
            decision=legacy_decision,
            signal_fingerprint=legacy_fingerprint,
            channel_id=legacy_trigger.channel_id,
            thread_ts=legacy_trigger.thread_ts,
        )
        DecisionLedger().record_delivery(
            str(record["decision_id"]),
            status="delivered",
            slack_message_ts="ts-legacy",
        )

    reworded = legacy_decision.model_copy(
        update={
            "verified_information": (
                "The observed state remains: flaky test test_retry failed on attempt 1."
            ),
            "next_action": "Stabilize the retry fixture.",
        }
    )
    deliveries: list[str] = []
    with bound_storage_scope(scope):
        outcome = ProactiveJudgementRunner(
            context_reader=_context_reader,
            delivery=lambda **_kwargs: deliveries.append("sent") or "ts-current",
            llm_factory=lambda: _StructuredLLM(reworded),
        ).run(current_trigger)

    assert outcome.status == "suppressed"
    assert deliveries == []


def test_failed_delivery_does_not_suppress_later_occurrence(scope: StorageScope) -> None:
    first = write_interaction(scope, session_id="session-failed-signal", suffix="failed")
    second = write_interaction(scope, session_id="session-retry-signal", suffix="retry")
    delivered: list[str] = []

    with bound_storage_scope(scope):
        failed = ProactiveJudgementRunner(
            context_reader=_context_reader,
            delivery=lambda **_kwargs: None,
            llm_factory=lambda: _StructuredLLM(_send_decision()),
        ).run(first)
        later = ProactiveJudgementRunner(
            context_reader=_context_reader,
            delivery=lambda **_kwargs: delivered.append("sent") or "ts-later",
            llm_factory=lambda: _StructuredLLM(_send_decision()),
        ).run(second)

    assert failed.status == "delivery_failed"
    assert later.status == "delivered"
    assert delivered == ["sent"]


def test_existing_send_intent_is_not_delivered_again_after_restart(scope: StorageScope) -> None:
    trigger = write_interaction(scope, session_id="session-restart")
    with bound_storage_scope(scope):
        DecisionLedger().record_decision(
            session_id=trigger.session_id,
            interaction_id="assistant-one",
            end_record_id=trigger.end_record_id,
            policy_name="master-judgement",
            policy_version=1,
            decision=_send_decision(),
            signal_fingerprint="fingerprint",
            channel_id=trigger.channel_id,
            thread_ts=trigger.thread_ts,
        )

    deliveries: list[str] = []
    runner = ProactiveJudgementRunner(
        context_reader=_context_reader,
        delivery=lambda **_kwargs: deliveries.append("sent") or "ts",
        llm_factory=lambda: _StructuredLLM(_send_decision()),
    )
    with bound_storage_scope(scope):
        outcome = runner.run(trigger)
        cursor = JudgementCursor().last(trigger.session_id)

    assert outcome.status == "already_judged"
    assert deliveries == []
    assert cursor is not None and cursor["end_record_id"] == trigger.end_record_id


def test_structured_judgement_failure_is_durably_suppressed(scope: StorageScope) -> None:
    trigger = write_interaction(scope, session_id="session-llm-failure")
    deliveries: list[str] = []
    runner = ProactiveJudgementRunner(
        context_reader=_context_reader,
        delivery=lambda **_kwargs: deliveries.append("sent") or "ts",
        llm_factory=_FailingStructuredLLM,
    )

    with bound_storage_scope(scope):
        outcome = runner.run(trigger)
        decision = DecisionLedger().recent(1)[0]
        cursor = JudgementCursor().last(trigger.session_id)

    assert outcome.status == "suppressed"
    assert deliveries == []
    assert decision["decision"] == "suppress"
    assert decision["rationale"] == "Structured judgement failed closed (RuntimeError)."
    assert cursor is not None and cursor["end_record_id"] == trigger.end_record_id


def test_delivery_failure_is_recorded_and_never_retried(scope: StorageScope) -> None:
    trigger = write_interaction(scope, session_id="session-delivery-failure")
    attempts: list[str] = []

    def _fail_delivery(**_kwargs: Any) -> str:
        attempts.append("attempted")
        raise ConnectionError("Slack unavailable")

    llm = _StructuredLLM(_send_decision())
    runner = ProactiveJudgementRunner(
        context_reader=_context_reader,
        delivery=_fail_delivery,
        llm_factory=lambda: llm,
    )
    with bound_storage_scope(scope):
        first = runner.run(trigger)
        second = runner.run(trigger)
        decision = DecisionLedger().recent(1)[0]

    assert first.status == "delivery_failed"
    assert second.status == "already_evaluated"
    assert attempts == ["attempted"]
    assert len(llm.prompts) == 1
    assert decision["delivery_status"] == "delivery_failed"
    assert decision["error_type"] == "ConnectionError"
