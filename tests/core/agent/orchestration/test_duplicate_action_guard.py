"""Hook-level tests for the duplicate-action guard's snapshot rules.

The end-to-end cases live in ``test_agent_actions_harness.py``, which drives a
whole action turn per case. These drive ``with_duplicate_action_call_guard``
directly so each ``before_tool_batch`` branch — full success, mixed, and
retain — is pinned on its own, along with the argument normalization that
decides whether two calls count as identical.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.turns.action_dedup import with_duplicate_action_call_guard
from core.llm.types import ToolCall
from core.tool.execution import ToolExecutionResult

# One emitted tool call: name, arguments, and whether the tool would succeed.
_Call = tuple[str, dict[str, Any], bool]


def _request(name: str, payload: dict[str, Any]) -> Any:
    call = ToolCall(id=f"call-{name}", name=name, input=payload)
    return type("Request", (), {"tool_call": call, "arguments": payload})()


def _label(payload: dict[str, Any]) -> str:
    return str(payload.get("command", payload.get("payload", "")))


def _drive(batches: list[list[_Call]]) -> list[str]:
    """Run ``batches`` through the guard and return the calls that executed."""
    hooks = with_duplicate_action_call_guard()
    executed: list[str] = []
    for batch in batches:
        hooks.before_tool_batch([ToolCall(id="c", name=n, input=p) for n, p, _ in batch])
        for name, payload, succeeds in batch:
            request = _request(name, payload)
            decision = hooks.before_tool_call(request)
            if decision is not None and decision.blocked:
                hooks.after_tool_call(request, ToolExecutionResult(content="", is_error=True))
                continue
            executed.append(_label(payload))
            hooks.after_tool_call(
                request, ToolExecutionResult(content="out", is_error=not succeeds)
            )
    return executed


HEALTH: _Call = ("slash_invoke", {"command": "/health", "args": []}, True)
INTEGRATIONS: _Call = ("slash_invoke", {"command": "/integrations", "args": ["list"]}, True)
INTEGRATIONS_FAILS: _Call = ("slash_invoke", {"command": "/integrations", "args": ["list"]}, False)
UNGUARDED: _Call = ("fleet_status", {"command": "n/a"}, True)


def test_a_call_that_failed_beside_a_success_may_retry_while_the_success_may_not() -> None:
    """A partly failed batch contributes only its successful calls to the snapshot.

    The failed call never ran to completion, so retrying it is legitimate; the
    one that did succeed must not repeat its side effect.
    """
    # Arrange / Act: /health succeeds, /integrations errors, then both re-emit.
    executed = _drive([[HEALTH, INTEGRATIONS_FAILS], [HEALTH, INTEGRATIONS]])

    # Assert: /health ran once, /integrations got its retry.
    assert executed == ["/health", "/integrations", "/integrations"]


def test_a_batch_of_unguarded_tools_does_not_clear_the_snapshot() -> None:
    """Only guarded calls form a batch, so an unguarded batch must not reset the guard."""
    # Arrange / Act: an unrelated tool runs between two identical /health batches.
    executed = _drive([[HEALTH], [UNGUARDED], [HEALTH]])

    # Assert: the replayed /health is still suppressed.
    assert executed == ["/health", "n/a"]


def test_shell_quiet_matches_whether_it_arrives_as_a_bool_or_a_string() -> None:
    """A model re-emitting ``quiet`` as ``"true"`` is repeating the same command."""
    # Arrange / Act.
    as_bool: _Call = ("shell_run", {"command": "pwd", "quiet": True}, True)
    as_string: _Call = ("shell_run", {"command": "pwd", "quiet": "true"}, True)
    executed = _drive([[as_bool], [as_string]])

    # Assert: the second spelling is recognized as the same call.
    assert executed == ["pwd"]


def test_cli_payload_matches_across_surrounding_whitespace() -> None:
    """Padding around a cli_exec payload does not make it a different command."""
    # Arrange / Act.
    plain: _Call = ("cli_exec", {"payload": "integrations verify"}, True)
    padded: _Call = ("cli_exec", {"payload": "  integrations verify  "}, True)
    executed = _drive([[plain], [padded]])

    # Assert.
    assert executed == ["integrations verify"]
