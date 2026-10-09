"""Read-only calls of one response run at once; hooks still run in order on the caller."""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Callable
from typing import Any

import pytest

from config.constants.tooling import OPENSRE_PARALLEL_TOOL_CALLS_ENV
from core.llm.types import ToolCall
from core.tool.contracts import RegisteredTool, SideEffectLevel
from core.tool.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionRequest,
    ToolExecutionResult,
    execute_tool_calls,
)

_BARRIER_TIMEOUT_SECONDS = 5.0
_marker: contextvars.ContextVar[str] = contextvars.ContextVar("parallel_test_marker", default="")


def _registered(
    name: str,
    run: Callable[..., Any],
    *,
    side_effect_level: SideEffectLevel | None = SideEffectLevel.READ_ONLY,
) -> RegisteredTool:
    return RegisteredTool(
        name=name,
        description="test tool",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": [],
            "additionalProperties": False,
        },
        source="knowledge",
        side_effect_level=side_effect_level,
        run=run,
    )


def _call(name: str) -> ToolCall:
    return ToolCall(id=f"{name}-id", name=name, input={"value": name})


class _Overlap:
    """Counts how many tool bodies run at the same moment."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0
        self.peak = 0
        self.order: list[str] = []

    def tool(self, name: str, *, gate: threading.Event | None = None) -> Callable[..., Any]:
        def run(value: str) -> dict[str, Any]:
            with self._lock:
                self._active += 1
                self.peak = max(self.peak, self._active)
                self.order.append(f"start:{name}")
            if gate is not None:
                gate.wait(_BARRIER_TIMEOUT_SECONDS)
            with self._lock:
                self._active -= 1
                self.order.append(f"end:{name}")
            return {"value": value}

        return run


def test_read_only_calls_run_at_once_and_answer_in_provider_order() -> None:
    # Each body waits for the other: sequential execution would break the barrier.
    barrier = threading.Barrier(2, timeout=_BARRIER_TIMEOUT_SECONDS)

    def meet(value: str) -> dict[str, Any]:
        barrier.wait()
        return {"value": value}

    tools = [_registered("read_a", meet), _registered("read_b", meet)]

    results = execute_tool_calls([_call("read_a"), _call("read_b")], tools, {})

    assert [r.is_error for r in results] == [False, False]
    assert [r.details for r in results] == [{"value": "read_a"}, {"value": "read_b"}]


def test_hooks_run_on_the_calling_thread_all_checks_before_any_result() -> None:
    caller = threading.get_ident()
    events: list[tuple[str, str]] = []
    threads: set[int] = set()

    def before(request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        threads.add(threading.get_ident())
        events.append(("before", request.tool_call.name))
        return None

    def after(request: ToolExecutionRequest, _result: ToolExecutionResult) -> None:
        threads.add(threading.get_ident())
        events.append(("after", request.tool_call.name))

    overlap = _Overlap()
    tools = [_registered(n, overlap.tool(n)) for n in ("read_a", "read_b", "read_c")]

    execute_tool_calls(
        [_call("read_a"), _call("read_b"), _call("read_c")],
        tools,
        {},
        hooks=ToolExecutionHooks(before_tool_call=before, after_tool_call=after),
    )

    assert threads == {caller}
    assert events == [
        ("before", "read_a"),
        ("before", "read_b"),
        ("before", "read_c"),
        ("after", "read_a"),
        ("after", "read_b"),
        ("after", "read_c"),
    ]


def test_a_mutating_call_runs_alone_between_read_only_groups() -> None:
    overlap = _Overlap()
    tools = [
        _registered("read_a", overlap.tool("read_a")),
        _registered("write", overlap.tool("write"), side_effect_level=SideEffectLevel.MUTATING),
        _registered("undeclared", overlap.tool("undeclared"), side_effect_level=None),
        _registered("read_b", overlap.tool("read_b")),
    ]

    execute_tool_calls(
        [_call("read_a"), _call("write"), _call("undeclared"), _call("read_b")], tools, {}
    )

    assert overlap.order == [
        "start:read_a",
        "end:read_a",
        "start:write",
        "end:write",
        "start:undeclared",
        "end:undeclared",
        "start:read_b",
        "end:read_b",
    ]


def test_the_kill_switch_runs_read_only_calls_one_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENSRE_PARALLEL_TOOL_CALLS_ENV, "0")
    overlap = _Overlap()
    tools = [_registered(n, overlap.tool(n)) for n in ("read_a", "read_b")]

    execute_tool_calls([_call("read_a"), _call("read_b")], tools, {})

    assert overlap.peak == 1
    assert overlap.order == ["start:read_a", "end:read_a", "start:read_b", "end:read_b"]


def test_a_check_that_ends_the_turn_skips_the_calls_after_it_in_the_group() -> None:
    overlap = _Overlap()
    tools = [_registered(n, overlap.tool(n)) for n in ("read_a", "read_b", "read_c")]
    started: list[str] = []

    def before(request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        if request.tool_call.name == "read_b":
            return BeforeToolCallResult(blocked=True, terminate=True, reason="approval pending")
        return None

    results = execute_tool_calls(
        [_call("read_a"), _call("read_b"), _call("read_c")],
        tools,
        {},
        hooks=ToolExecutionHooks(before_tool_call=before),
        on_call_start=lambda tc: started.append(tc.name),
    )

    assert overlap.order == ["start:read_a", "end:read_a"]
    assert started == ["read_a", "read_b"]
    assert not results[0].is_error
    assert results[1].terminate and results[1].metadata.get("blocked") is True
    assert results[2].metadata.get("skipped") is True


def test_a_raising_body_in_a_group_still_reaches_the_after_hook() -> None:
    seen: list[tuple[str, bool]] = []

    def boom(value: str) -> dict[str, Any]:
        raise RuntimeError(f"{value} unreachable")

    def fine(value: str) -> dict[str, Any]:
        return {"value": value}

    def after(request: ToolExecutionRequest, result: ToolExecutionResult) -> None:
        seen.append((request.tool_call.name, result.is_error))

    results = execute_tool_calls(
        [_call("read_a"), _call("read_b")],
        [_registered("read_a", boom), _registered("read_b", fine)],
        {},
        hooks=ToolExecutionHooks(after_tool_call=after),
    )

    assert results[0].is_error and "read_a unreachable" in str(results[0].content)
    assert not results[1].is_error
    assert seen == [("read_a", True), ("read_b", False)]


def test_bodies_see_the_callers_context_variables() -> None:
    def read_marker(value: str) -> dict[str, Any]:
        return {"marker": _marker.get(), "value": value}

    token = _marker.set("turn-42")
    try:
        results = execute_tool_calls(
            [_call("read_a"), _call("read_b")],
            [_registered("read_a", read_marker), _registered("read_b", read_marker)],
            {},
        )
    finally:
        _marker.reset(token)

    assert [r.details["marker"] for r in results] == ["turn-42", "turn-42"]
