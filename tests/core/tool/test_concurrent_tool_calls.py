"""Run order, result order, skip marking and concurrency for one response's tool calls."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope, current_scope
from core.domain.types.tools import ToolRole
from core.llm.types import ToolCall
from core.tool.contracts import RegisteredTool, SideEffectLevel
from core.tool.execution import (
    ToolExecutionHooks,
    ToolExecutionRequest,
    ToolExecutionResult,
    execute_tool_calls,
)

# Generous so a rendezvous never times out under CI load; only a serial executor waits it out.
_RENDEZVOUS_SECONDS = 10.0
# How long a "slow" read takes; the executor must not start later work before it ends.
_SLOW_READ_SECONDS = 0.05

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": [],
    "additionalProperties": False,
}


def _registered(
    name: str,
    run: Callable[..., Any],
    *,
    side_effect_level: SideEffectLevel = SideEffectLevel.READ_ONLY,
    concurrency_safe: bool = False,
) -> RegisteredTool:
    return RegisteredTool(
        name=name,
        description="test tool",
        input_schema=_SCHEMA,
        source="knowledge",
        run=run,
        side_effect_level=side_effect_level,
        concurrency_safe=concurrency_safe,
    )


def _read(name: str, run: Callable[..., Any]) -> RegisteredTool:
    return _registered(name, run, concurrency_safe=True)


def _call(name: str) -> ToolCall:
    return ToolCall(id=f"{name}-1", name=name, input={"value": name})


@pytest.mark.parametrize("reads_run_together", [False, True])
def test_calls_start_in_provider_order_and_a_turn_ending_result_skips_the_rest(
    reads_run_together: bool,
) -> None:
    # Arrange: two reads, a write that ends the turn, then a read it must skip.
    ran: list[str] = []
    started: list[str] = []

    def body(*, ends_turn: bool = False) -> Callable[..., ToolExecutionResult]:
        def run(value: str) -> ToolExecutionResult:
            ran.append(value)
            return ToolExecutionResult(content=value, details={"ran": value}, terminate=ends_turn)

        return run

    tools = [
        _registered("read_a", body(), concurrency_safe=reads_run_together),
        _registered("read_b", body(), concurrency_safe=reads_run_together),
        _registered("write", body(ends_turn=True), side_effect_level=SideEffectLevel.MUTATING),
        _registered("read_c", body(), concurrency_safe=reads_run_together),
    ]
    calls = [_call("read_a"), _call("read_b"), _call("write"), _call("read_c")]

    # Act
    results = execute_tool_calls(calls, tools, {}, on_call_start=lambda tc: started.append(tc.name))

    # Assert: one result per call, in provider order; the skipped call never started.
    assert started == ["read_a", "read_b", "write"]
    assert sorted(ran[:2]) == ["read_a", "read_b"]
    assert ran[2:] == ["write"]
    assert [result.details for result in results[:3]] == [
        {"ran": "read_a"},
        {"ran": "read_b"},
        {"ran": "write"},
    ]
    assert results[2].terminate is True
    skipped = results[3]
    assert skipped.is_error is True
    assert skipped.metadata == {"tool_name": "read_c", "skipped": True}
    assert skipped.content == (
        "Not run: write ended the turn, so this call was skipped. "
        "Re-issue it next turn if it is still needed."
    )


def test_concurrency_safe_reads_overlap_and_run_in_the_callers_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: each read waits until the other one is running, so only overlap passes.
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "infrastructure.analytics.capture.capture_agent_tool_call_completed",
        lambda **properties: events.append(properties),
    )
    both_running = threading.Barrier(2, timeout=_RENDEZVOUS_SECONDS)
    caller = threading.get_ident()
    seen: dict[str, tuple[StorageScope | None, bool]] = {}

    def run(value: str) -> dict[str, Any]:
        both_running.wait()
        seen[value] = (current_scope(), threading.get_ident() != caller)
        return {"read": value}

    scope = StorageScope(principal=Principal.org("org_A"), actor=Actor(id="u1"))
    tools = [_read("logs", run), _read("metrics", run)]

    # Act
    with bound_storage_scope(scope):
        results = execute_tool_calls([_call("logs"), _call("metrics")], tools, {}, iteration=3)

    # Assert: worker threads ran both under the caller's storage scope; one event per call.
    assert [result.details for result in results] == [{"read": "logs"}, {"read": "metrics"}]
    assert seen == {"logs": (scope, True), "metrics": (scope, True)}
    assert sorted((event["tool_call_index"], event["outcome"]) for event in events) == [
        (0, "ok"),
        (1, "ok"),
    ]


def test_a_call_that_is_not_concurrency_safe_runs_alone_between_reads() -> None:
    # Arrange: slow reads on both sides of a write; record what had finished at each start.
    finished: set[str] = set()
    finished_lock = threading.Lock()
    finished_at_start: dict[str, set[str]] = {}

    def finish(value: str) -> None:
        with finished_lock:
            finished.add(value)

    def note_start(tc: ToolCall) -> None:
        with finished_lock:
            finished_at_start[tc.name] = set(finished)

    def slow_read(value: str) -> dict[str, Any]:
        time.sleep(_SLOW_READ_SECONDS)
        finish(value)
        return {"read": value}

    def write(value: str) -> dict[str, Any]:
        finish(value)
        return {"wrote": value}

    tools = [
        _read("read_a", slow_read),
        _read("read_b", slow_read),
        _registered("write", write, side_effect_level=SideEffectLevel.MUTATING),
        _read("read_c", slow_read),
        _read("read_d", slow_read),
    ]
    calls = [_call(name) for name in ("read_a", "read_b", "write", "read_c", "read_d")]

    # Act
    results = execute_tool_calls(calls, tools, {}, on_call_start=note_start)

    # Assert: the write waits for both earlier reads; the later reads wait for the write.
    assert not any(result.is_error for result in results)
    assert finished_at_start == {
        "read_a": set(),
        "read_b": set(),
        "write": {"read_a", "read_b"},
        "read_c": {"read_a", "read_b", "write"},
        "read_d": {"read_a", "read_b", "write"},
    }


def test_a_host_cancel_skips_reads_that_have_not_started(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: two workers; the first read cancels the turn while the second still runs.
    monkeypatch.setattr("core.tool.execution.MAX_CONCURRENT_TOOL_CALLS", 2)
    both_running = threading.Barrier(2, timeout=_RENDEZVOUS_SECONDS)
    cancelled = threading.Event()
    started: list[str] = []

    def cancels(value: str) -> dict[str, Any]:
        both_running.wait()
        cancelled.set()
        return {"read": value}

    def outlives_the_cancel(value: str) -> dict[str, Any]:
        both_running.wait()
        cancelled.wait(_RENDEZVOUS_SECONDS)
        return {"read": value}

    def never_runs(value: str) -> dict[str, Any]:
        return {"error": f"{value} ran after the cancel"}

    tools = [
        _read("read_1", cancels),
        _read("read_2", outlives_the_cancel),
        _read("read_3", never_runs),
        _read("read_4", never_runs),
    ]
    calls = [_call(name) for name in ("read_1", "read_2", "read_3", "read_4")]

    # Act
    results = execute_tool_calls(
        calls,
        tools,
        {},
        should_stop=cancelled.is_set,
        on_call_start=lambda tc: started.append(tc.name),
    )

    # Assert: the running read keeps its result; reads not yet started never start.
    assert started == ["read_1", "read_2"]
    assert [result.details for result in results[:2]] == [{"read": "read_1"}, {"read": "read_2"}]
    for name, result in zip(("read_3", "read_4"), results[2:], strict=True):
        assert result.metadata == {"tool_name": name, "skipped": True}
        assert str(result.content).startswith("Not run: the turn was cancelled")


def test_host_callbacks_never_overlap_while_reads_run_together() -> None:
    # Arrange: three reads in flight at once; each host callback checks that it runs alone.
    all_running = threading.Barrier(3, timeout=_RENDEZVOUS_SECONDS)
    in_callback = threading.Lock()
    overlapping: list[str] = []

    def exclusive(label: str) -> None:
        if not in_callback.acquire(blocking=False):
            overlapping.append(label)
            return
        try:
            time.sleep(0.005)
        finally:
            in_callback.release()

    def before(request: ToolExecutionRequest) -> None:
        exclusive(f"before {request.tool_call.name}")

    def after(request: ToolExecutionRequest, _result: ToolExecutionResult) -> None:
        exclusive(f"after {request.tool_call.name}")

    def run(value: str) -> dict[str, Any]:
        all_running.wait()
        return {"read": value}

    names = ("read_a", "read_b", "read_c")

    # Act
    results = execute_tool_calls(
        [_call(name) for name in names],
        [_read(name, run) for name in names],
        {},
        hooks=ToolExecutionHooks(before_tool_call=before, after_tool_call=after),
        on_call_start=lambda tc: exclusive(f"start {tc.name}"),
    )

    # Assert
    assert not any(result.is_error for result in results)
    assert overlapping == []


@pytest.mark.parametrize(
    "unsafe",
    [
        {"side_effect_level": SideEffectLevel.MUTATING},
        {"role": ToolRole.BOOKKEEPING},
        {"requires_approval": True},
        {"accepts_runtime_context": True},
    ],
)
def test_only_plain_reads_may_declare_concurrency_safe(unsafe: dict[str, Any]) -> None:
    fields: dict[str, Any] = {"side_effect_level": SideEffectLevel.READ_ONLY, **unsafe}

    with pytest.raises(ValueError, match="cannot be concurrency_safe"):
        RegisteredTool(
            name="unsafe_read",
            description="test tool",
            input_schema=_SCHEMA,
            source="knowledge",
            run=lambda value: {"read": value},
            concurrency_safe=True,
            **fields,
        )
