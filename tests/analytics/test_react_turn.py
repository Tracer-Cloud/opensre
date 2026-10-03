from __future__ import annotations

import pytest

from core.agent.run_io import AgentRunResult
from infrastructure.analytics import capture
from infrastructure.analytics.events import Event
from infrastructure.analytics.react_turn import emit_react_turn_completed, resolve_react_stop_reason


class _StubLLM:
    _model = "claude-sonnet-4-6"
    _provider_label = "Anthropic"


class _StubAnalytics:
    def __init__(self) -> None:
        self.events: list[tuple[Event, dict[str, object] | None]] = []

    def capture(self, event: Event, properties: dict[str, object] | None = None) -> None:
        self.events.append((event, properties))


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"hit_iteration_cap": False, "tool_calls_executed": 2}, "completed"),
        ({"hit_iteration_cap": True, "tool_calls_executed": 2}, "iteration_cap"),
        ({"hit_iteration_cap": False, "tool_calls_executed": 0}, "no_tools_needed"),
        ({"hit_iteration_cap": False, "tool_calls_executed": 0, "error": RuntimeError()}, "error"),
        ({"hit_iteration_cap": False, "tool_calls_executed": 0, "cancelled": True}, "cancelled"),
    ],
)
def test_resolve_react_stop_reason(kwargs: dict[str, object], expected: str) -> None:
    assert resolve_react_stop_reason(**kwargs) == expected  # type: ignore[arg-type]


def test_capture_react_turn_completed_emits_required_properties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(capture, "get_analytics", lambda: stub)

    capture.capture_react_turn_completed(
        phase="action",
        llm_iterations_used=3,
        llm_iteration_cap=6,
        hit_iteration_cap=False,
        stop_reason="completed",
        tool_calls_executed=2,
        duration_ms=1200,
        cli_session_id="sess-1",
        cli_turn_kind="agent",
        llm_provider="anthropic",
        llm_model="claude-sonnet-4-6",
        prompt_turn_id="turn-1",
    )

    assert stub.events == [
        (
            Event.REACT_TURN_COMPLETED,
            {
                "phase": "action",
                "llm_iterations_used": 3,
                "llm_iteration_cap": 6,
                "hit_iteration_cap": False,
                "stop_reason": "completed",
                "tool_calls_executed": 2,
                "duration_ms": 1200,
                "cli_session_id": "sess-1",
                "cli_turn_kind": "agent",
                "llm_provider": "anthropic",
                "llm_model": "claude-sonnet-4-6",
                "prompt_turn_id": "turn-1",
            },
        )
    ]


def test_emit_react_turn_completed_sets_hit_iteration_cap_from_stop_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        "infrastructure.analytics.react_turn.capture_react_turn_completed",
        lambda **kwargs: captured.append(kwargs),
    )

    emit_react_turn_completed(
        phase="gather",
        result=AgentRunResult(
            messages=[],
            final_text="",
            hit_iteration_cap=True,
            llm_iterations_used=4,
        ),
        iteration_cap=4,
        duration_ms=900,
        llm=_StubLLM(),
        session=None,
    )

    assert captured == [
        {
            "phase": "gather",
            "llm_iterations_used": 4,
            "llm_iteration_cap": 4,
            "hit_iteration_cap": True,
            "stop_reason": "iteration_cap",
            "tool_calls_executed": 0,
            "duration_ms": 900,
            "cli_session_id": "",
            "cli_turn_kind": "agent",
            "llm_provider": "anthropic",
            "llm_model": "claude-sonnet-4-6",
            "prompt_turn_id": None,
            "loop_stop_reason": "iteration_cap",
            "error_type": "",
            "error_message": "",
        }
    ]


def test_hard_stops_keep_the_dashboard_stop_reason_but_name_the_loop_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: two runs that ended on different hard stops.
    stub = _StubAnalytics()
    monkeypatch.setattr(capture, "get_analytics", lambda: stub)

    # Act
    for loop_reason in ("goal_unverified", "stagnation_limit"):
        emit_react_turn_completed(
            phase="action",
            result=AgentRunResult(
                messages=[],
                final_text="handoff",
                hit_iteration_cap=True,
                stop_reason=loop_reason,
                llm_iterations_used=5,
            ),
            iteration_cap=64,
            duration_ms=900,
            llm=_StubLLM(),
        )

    # Assert: dashboards still see iteration_cap; the loop reason tells them apart.
    recorded = [
        (properties["stop_reason"], properties["loop_stop_reason"])
        for _event, properties in stub.events
        if properties is not None
    ]
    assert recorded == [
        ("iteration_cap", "goal_unverified"),
        ("iteration_cap", "stagnation_limit"),
    ]


def test_a_run_that_raised_records_the_exception_type_and_a_redacted_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    stub = _StubAnalytics()
    monkeypatch.setattr(capture, "get_analytics", lambda: stub)
    token = "ghp_" + "c" * 36

    # Act
    emit_react_turn_completed(
        phase="action",
        result=None,
        iteration_cap=64,
        duration_ms=10,
        llm=_StubLLM(),
        error=RuntimeError(f"provider rejected {token}"),
    )

    # Assert
    (_event, properties) = stub.events[0]
    assert properties is not None
    assert properties["stop_reason"] == properties["loop_stop_reason"] == "error"
    assert properties["error_type"] == "RuntimeError"
    assert properties["error_message"] == "provider rejected [REDACTED:github_pat]"
