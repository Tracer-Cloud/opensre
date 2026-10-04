"""ReAct loop emits ``span_kind=llm`` around each model invoke."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from config.constants import OPENSRE_OPERATIONS_LOG_PATH_ENV
from core.agent import Agent
from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
from core.llm.types import AgentLLMResponse, SchemaDescribedTool, ToolCall
from core.tool import RuntimeTool
from infrastructure.observability.operations_log import read_operations
from infrastructure.observability.trace.spans import (
    NoopSessionTraceStore,
    bind_session_trace,
    set_session_trace_store,
)
from surfaces.interactive_shell.session.trace_store import JsonlSessionTraceStore


@pytest.fixture(autouse=True)
def _reset_session_trace_store() -> Any:
    set_session_trace_store(NoopSessionTraceStore())
    yield
    set_session_trace_store(NoopSessionTraceStore())


class _NoToolLLM:
    model_id = "test-model"

    def tool_schemas(self, _tools: list[Any]) -> list[dict[str, Any]]:
        return []

    def invoke(
        self,
        _messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = (system, tools)
        return AgentLLMResponse(content="done", tool_calls=[], raw_content=None)

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[object]) -> dict[str, object]:
        return {"role": "assistant", "content": content, "tool_calls": tool_calls}

    @staticmethod
    def build_tool_result_message(
        _tool_calls: list[object], _results: list[object]
    ) -> dict[str, object]:
        return {"role": "tool", "content": "[]"}


class _ToolThenDoneLLM(_NoToolLLM):
    def __init__(self) -> None:
        self.calls = 0

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        return [
            {
                "name": getattr(tool, "name", "tool"),
                "description": "",
                "parameters": getattr(tool, "parameters", {}),
            }
            for tool in tools
        ]

    def invoke(
        self,
        _messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = (system, tools)
        self.calls += 1
        if self.calls == 1:
            return AgentLLMResponse(
                content="",
                tool_calls=[ToolCall(id="c1", name="echo", input={})],
                raw_content=None,
            )
        return AgentLLMResponse(content="done", tool_calls=[], raw_content=None)


class _EchoTool:
    name = "echo"
    description = "echo"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def validate_public_input(self, value: dict[str, Any]) -> str | None:
        _ = value
        return None

    def extract_params(self, resolved: dict[str, Any]) -> dict[str, Any]:
        _ = resolved
        return {}

    def run(self, **_kwargs: Any) -> dict[str, bool]:
        return {"ok": True}


def _activate_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    session_id: str,
) -> Path:
    monkeypatch.setattr(
        "core.agent_harness.session.persistence.jsonl_store.session_path",
        lambda requested_session_id: tmp_path / f"{requested_session_id}.jsonl",
    )
    storage = JsonlSessionStore()
    path = tmp_path / f"{session_id}.jsonl"
    path.write_text(
        json.dumps({"type": "session", "version": 2, "id": session_id}) + "\n",
        encoding="utf-8",
    )
    set_session_trace_store(JsonlSessionTraceStore(store=storage))
    return path


def _trace_spans(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").strip().splitlines()
        if json.loads(line).get("type") == "trace_span"
    ]


def test_agent_run_emits_llm_span_when_sink_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "sess-llm-span"
    path = _activate_trace(tmp_path, monkeypatch, session_id=session_id)

    agent = Agent(
        llm=_NoToolLLM(),
        system="sys",
        tools=[],
        resolved_integrations={},
        max_iterations=1,
    )
    with bind_session_trace(session_id):
        result = agent.run([{"role": "user", "content": "hello"}])

    assert result.final_text == "done"
    spans = _trace_spans(path)
    llm_spans = [s for s in spans if s.get("span_kind") == "llm"]
    assert len(llm_spans) == 1
    assert llm_spans[0]["name"] == "test-model"
    assert llm_spans[0]["status"] == "ok"
    assert llm_spans[0]["attributes"]["iteration"] == 0
    assert llm_spans[0]["attributes"]["has_tool_calls"] is False
    assert llm_spans[0]["attributes"]["tool_call_count"] == 0
    assert "duration_ms" in llm_spans[0]

    loop_span = next(s for s in spans if s.get("span_kind") == "loop")
    assert loop_span["name"] == "react_loop"
    assert loop_span["attributes"]["outcome"] == "no_tools_needed"
    assert loop_span["attributes"]["iterations_used"] == 1
    assert loop_span["attributes"]["executed_count"] == 0

    iteration_span = next(s for s in spans if s.get("span_kind") == "loop_iteration")
    assert iteration_span["name"] == "react_iteration"
    assert iteration_span["attributes"]["iteration"] == 0
    assert iteration_span["attributes"]["outcome"] == "no_tools_needed"
    assert iteration_span["attributes"]["should_stop"] is True


def test_agent_run_skips_llm_span_when_noop() -> None:
    agent = Agent(
        llm=_NoToolLLM(),
        system="sys",
        tools=[],
        resolved_integrations={},
        max_iterations=1,
    )
    result = agent.run([{"role": "user", "content": "hello"}])
    assert result.final_text == "done"


def test_agent_run_writes_operations_log_without_prompt_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_path = tmp_path / "operations.jsonl"
    monkeypatch.setenv(OPENSRE_OPERATIONS_LOG_PATH_ENV, str(log_path))
    agent = Agent(
        llm=_NoToolLLM(),
        system="sys",
        tools=[],
        resolved_integrations={},
        max_iterations=1,
    )

    result = agent.run([{"role": "user", "content": "hello secret prompt"}])

    assert result.final_text == "done"
    records = read_operations(path=log_path)
    events = [record["event"] for record in records]
    assert events == [
        "agent_loop_started",
        "agent_loop_iteration",
        "agent_loop_finished",
    ]
    run_ids = {record["data"]["run_id"] for record in records}
    assert len(run_ids) == 1
    finished = records[-1]["data"]
    assert finished["stop_reason"] == "no_tools_needed"
    assert finished["iterations_used"] == 1
    assert finished["final_text_chars"] == len("done")
    serialized = json.dumps(records)
    assert "hello secret prompt" not in serialized
    assert '"done"' not in serialized


def test_agent_run_emits_loop_iteration_outcomes_for_tool_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "sess-react-loop-tool-round"
    path = _activate_trace(tmp_path, monkeypatch, session_id=session_id)
    agent = Agent(
        llm=_ToolThenDoneLLM(),
        system="sys",
        tools=[_EchoTool()],
        resolved_integrations={},
        max_iterations=3,
    )

    with bind_session_trace(session_id):
        result = agent.run([{"role": "user", "content": "hello"}])

    assert result.final_text == "done"
    assert result.llm_iterations_used == 2
    spans = _trace_spans(path)
    iteration_spans = [s for s in spans if s.get("span_kind") == "loop_iteration"]
    assert [span["attributes"]["outcome"] for span in iteration_spans] == [
        "tool_observed",
        "completed",
    ]
    assert iteration_spans[0]["attributes"]["requested_tool_count"] == 1
    assert iteration_spans[0]["attributes"]["tool_error_count"] == 0
    assert iteration_spans[0]["attributes"]["should_stop"] is False

    loop = next(s for s in spans if s.get("span_kind") == "loop")
    assert loop["attributes"]["outcome"] == "completed"
    assert loop["attributes"]["iterations_used"] == 2
    assert loop["attributes"]["executed_count"] == 1


class _FailingLLM:
    """A provider whose every request raises ``message``."""

    model_id: str | None = "test-model"

    def __init__(self, message: str) -> None:
        self._message = message

    def tool_schemas(self, tools: Sequence[SchemaDescribedTool]) -> list[dict[str, Any]]:
        return [{"name": tool.name} for tool in tools]

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = (messages, system, tools)
        raise RuntimeError(self._message)


def test_a_failed_run_puts_the_redacted_exception_text_on_its_error_spans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: the provider call raises with a credential in its message.
    session_id = "sess-react-loop-error"
    path = _activate_trace(tmp_path, monkeypatch, session_id=session_id)
    token = "ghp_" + "d" * 36
    agent: Agent[RuntimeTool] = Agent(
        llm=_FailingLLM(f"upstream rejected {token}"),
        system="sys",
        tools=[],
        resolved_integrations={},
        max_iterations=2,
    )

    # Act
    with bind_session_trace(session_id), pytest.raises(RuntimeError):
        agent.run([{"role": "user", "content": "hello"}])

    # Assert: both the iteration and the loop span say what failed, redacted.
    spans = _trace_spans(path)
    for kind in ("loop_iteration", "loop"):
        span = next(s for s in spans if s.get("span_kind") == kind)
        assert span["status"] == "error"
        assert span["attributes"]["exception_type"] == "RuntimeError"
        assert span["attributes"]["exception_message"] == (
            "upstream rejected [REDACTED:github_pat]"
        )
