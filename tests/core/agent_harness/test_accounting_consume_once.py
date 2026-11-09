"""Turn accounting is consume-once — no stale prompt across dispatches."""

from __future__ import annotations

from typing import Any

from core.agent_harness.runtime import TurnBinding
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.turns.headless_adapters import NullToolProvider
from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


class _SpyAccounting:
    def __init__(self, label: str) -> None:
        self.label = label
        self.finalized: list[str] = []

    def record_action_result(self, _result: Any) -> None:
        return None

    def finalize(self, result: TurnResult) -> TurnResult:
        self.finalized.append(self.label)
        return result


def _empty_result() -> TurnResult:
    return TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=True,
        ),
        assistant_response_text="",
    )


def _stub_dispatch(monkeypatch: Any) -> None:
    """Skip the real turn engine; only exercise accounting take/finalize."""

    def _dispatch(message: str, session: Any, bindings: Any) -> TurnResult:
        _ = (message, session)
        bindings.accounting.record_action_result(None)  # type: ignore[arg-type]
        return bindings.accounting.finalize(_empty_result())

    monkeypatch.setattr(
        "core.agent_harness.turns.headless_agent.dispatch_chat_turn",
        _dispatch,
    )


def test_dispatch_consumes_bound_accounting(monkeypatch: Any) -> None:
    _stub_dispatch(monkeypatch)
    session = SessionCore(store=InMemorySessionStore())
    first = _SpyAccounting("first")
    agent = InMemoryHeadlessBuild(session=session).agent(tools=NullToolProvider())
    agent.bind_turn(TurnBinding(accounting=first))
    agent.dispatch("one")
    assert first.finalized == ["first"]
    agent.dispatch("two")
    assert first.finalized == ["first"]


def test_second_dispatch_without_rebind_does_not_reuse_prior_accounting(
    monkeypatch: Any,
) -> None:
    _stub_dispatch(monkeypatch)
    session = SessionCore(store=InMemorySessionStore())
    first = _SpyAccounting("first")
    agent = InMemoryHeadlessBuild(session=session).agent(tools=NullToolProvider())
    agent.bind_turn(TurnBinding(accounting=first))
    agent.dispatch("one")
    agent.dispatch("two")
    assert first.finalized == ["first"]


def test_bind_turn_supplies_fresh_accounting_each_message(monkeypatch: Any) -> None:
    _stub_dispatch(monkeypatch)
    session = SessionCore(store=InMemorySessionStore())
    agent = InMemoryHeadlessBuild(session=session).agent(tools=NullToolProvider())
    a = _SpyAccounting("a")
    b = _SpyAccounting("b")
    agent.bind_turn(TurnBinding(accounting=a))
    agent.dispatch("one")
    agent.bind_turn(TurnBinding(accounting=b))
    agent.dispatch("two")
    assert a.finalized == ["a"]
    assert b.finalized == ["b"]
