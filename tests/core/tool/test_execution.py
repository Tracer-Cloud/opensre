from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from core.agent import Agent
from core.domain.types.tools import ToolRole
from core.llm.types import AgentLLMResponse, ToolCall
from core.provider import ProviderHooks, ProviderRequest
from core.tool.contracts import AgentTool, AgentToolContext, RegisteredTool
from core.tool.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionPatch,
    ToolExecutionRequest,
    ToolExecutionResult,
    compose_tool_execution_hooks,
    execute_tool_calls,
    execute_tools,
    response_batch_violation,
)


def _schema(required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": required or [],
        "additionalProperties": False,
    }


def _tool(
    name: str = "echo",
    *,
    execute: Any | None = None,
    role: ToolRole = ToolRole.ACTION,
    source: str = "agent",
) -> AgentTool:
    return AgentTool(
        name=name,
        description="test tool",
        input_schema=_schema(["value"]),
        execute=execute or (lambda args, _ctx: {"value": args["value"]}),
        role=role,
        source=source,
    )


def _call(name: str = "echo", value: str = "ok") -> ToolCall:
    return ToolCall(id=f"{name}-1", name=name, input={"value": value})


def test_execute_tool_calls_validates_arguments_before_execution() -> None:
    called = False

    def execute(_args: dict[str, Any], _ctx: AgentToolContext) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"ok": True}

    result = execute_tool_calls(
        [ToolCall(id="c1", name="echo", input={})],
        [_tool(execute=execute)],
        {},
    )[0]

    assert result.is_error is True
    assert "missing required args" in str(result.content)
    assert called is False
    assert execute_tools([ToolCall(id="c1", name="echo", input={})], [_tool()], {}) == [
        {"error": result.content}
    ]


def test_success_payload_with_error_none_reaches_agent() -> None:
    """A success dict carrying ``"error": None`` must reach the agent unchanged,
    not be flagged as a failure on the mere presence of the key (#3889)."""
    payload = {"available": True, "events": [{"id": 1}], "error": None}

    result = execute_tool_calls([_call()], [_tool(execute=lambda _a, _c: payload)], {})[0]

    assert result.is_error is False
    assert result.details == payload
    # compat_payload returns the full payload for old call sites, not {"error": ...}.
    assert execute_tools([_call()], [_tool(execute=lambda _a, _c: payload)], {}) == [payload]


def test_truthy_error_still_flagged_as_failure() -> None:
    """A real error message is still surfaced as a failure (#3889)."""
    result = execute_tool_calls([_call()], [_tool(execute=lambda _a, _c: {"error": "boom"})], {})[0]

    assert result.is_error is True
    assert result.content == "boom"


def test_before_hook_can_block_with_structured_result() -> None:
    def before(_request: ToolExecutionRequest) -> BeforeToolCallResult:
        return BeforeToolCallResult(blocked=True, reason="blocked", details={"policy": "deny"})

    result = execute_tool_calls(
        [_call()],
        [_tool()],
        {},
        hooks=ToolExecutionHooks(before_tool_call=before),
    )[0]

    assert result.is_error is True
    assert result.content == "blocked"
    assert result.details == {"policy": "deny"}


def test_composed_before_hooks_stop_at_first_block() -> None:
    seen: list[str] = []

    def first(_request: ToolExecutionRequest) -> None:
        seen.append("first")

    def deny(_request: ToolExecutionRequest) -> BeforeToolCallResult:
        seen.append("deny")
        return BeforeToolCallResult(blocked=True, reason="denied")

    def never(_request: ToolExecutionRequest) -> None:
        seen.append("never")

    hooks = compose_tool_execution_hooks(
        ToolExecutionHooks(before_tool_call=first),
        ToolExecutionHooks(before_tool_call=deny),
        ToolExecutionHooks(before_tool_call=never),
    )

    result = execute_tool_calls([_call()], [_tool()], {}, hooks=hooks)[0]

    assert result.is_error is True
    assert result.content == "denied"
    assert seen == ["first", "deny"]


def test_composed_after_hooks_receive_prior_patches() -> None:
    seen: list[str] = []

    def first(
        _request: ToolExecutionRequest,
        _result: ToolExecutionResult,
    ) -> ToolExecutionPatch:
        return ToolExecutionPatch(content="first", metadata={"first": True})

    def second(
        _request: ToolExecutionRequest,
        result: ToolExecutionResult,
    ) -> ToolExecutionPatch:
        seen.append(str(result.content))
        return ToolExecutionPatch(content="second", metadata={"second": True})

    hooks = compose_tool_execution_hooks(
        ToolExecutionHooks(after_tool_call=first),
        ToolExecutionHooks(after_tool_call=second),
    )

    result = execute_tool_calls([_call()], [_tool()], {}, hooks=hooks)[0]

    assert result.content == "second"
    assert result.metadata == {"tool_name": "echo", "first": True, "second": True}
    assert seen == ["first"]


def test_before_hook_receives_executed_tools_source() -> None:
    # Arrange: capture the source handed to the before-hook. The source must be
    # the executed tool's own source, resolved O(1) via the tool_map — not a
    # linear scan, and never "unknown" for a known tool.
    captured: dict[str, str] = {}

    def before(request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        captured["source"] = request.source
        return None

    tool = _tool("dd_echo", source="datadog")

    # Act
    execute_tool_calls(
        [_call("dd_echo", "ok")],
        [tool],
        {},
        hooks=ToolExecutionHooks(before_tool_call=before),
    )

    # Assert
    assert captured["source"] == "datadog"


def test_after_hook_can_patch_result_and_terminate() -> None:
    def after(
        _request: ToolExecutionRequest,
        _result: ToolExecutionResult,
    ) -> ToolExecutionPatch:
        return ToolExecutionPatch(content="patched", details={"patched": True}, terminate=True)

    result = execute_tool_calls(
        [_call()],
        [_tool()],
        {},
        hooks=ToolExecutionHooks(after_tool_call=after),
    )[0]

    assert result.content == "patched"
    assert result.details == {"patched": True}
    assert result.terminate is True


def test_after_hook_runs_when_tool_raises() -> None:
    """Raised exceptions become error results and still notify after_tool_call."""
    seen: list[str] = []

    def execute(_args: dict[str, Any], _ctx: AgentToolContext) -> dict[str, Any]:
        raise ConnectionError("Connection to 172.29.15.185 timed out. (connect timeout=10)")

    def after(request: ToolExecutionRequest, result: ToolExecutionResult) -> None:
        seen.append(request.source)
        assert result.is_error
        assert "timed out" in str(result.content)

    result = execute_tool_calls(
        [_call()],
        [_tool(execute=execute, source="grafana")],
        {},
        hooks=ToolExecutionHooks(after_tool_call=after),
    )[0]

    assert result.is_error
    assert seen == ["grafana"]


def test_partial_tool_update_events_are_forwarded() -> None:
    updates: list[tuple[str, Any]] = []

    def execute(args: dict[str, Any], ctx: AgentToolContext) -> dict[str, Any]:
        ctx.emit_update({"seen": args["value"]})
        return {"done": True}

    def on_update(request: ToolExecutionRequest, update: Any) -> None:
        updates.append((request.tool_call.name, update))

    execute_tool_calls(
        [_call(value="abc")],
        [_tool(execute=execute)],
        {},
        hooks=ToolExecutionHooks(on_tool_update=on_update),
    )

    assert updates == [("echo", {"seen": "abc"})]


def test_registered_tool_receives_runtime_context_only_when_opted_in() -> None:
    seen: dict[str, Any] = {}

    def run(value: str, context: AgentToolContext) -> dict[str, Any]:
        seen["value"] = value
        seen["resource"] = context.resources["marker"]
        return {"ok": True}

    registered = RegisteredTool(
        name="contextual_echo",
        description="test registered tool",
        input_schema=_schema(["value"]),
        source="knowledge",
        run=run,
        accepts_runtime_context=True,
    )

    result = execute_tool_calls(
        [_call("contextual_echo", "abc")],
        [registered],
        {},
        tool_resources={"marker": "runtime"},
    )[0]

    assert result.is_error is False
    assert seen == {"value": "abc", "resource": "runtime"}


def test_registered_tool_without_context_opt_in_keeps_plain_run_contract() -> None:
    def run(value: str) -> dict[str, Any]:
        return {"value": value}

    registered = RegisteredTool(
        name="plain_registered_echo",
        description="test registered tool",
        input_schema=_schema(["value"]),
        source="knowledge",
        run=run,
    )

    result = execute_tool_calls(
        [_call("plain_registered_echo", "abc")],
        [registered],
        {},
        tool_resources={"marker": "runtime"},
    )[0]

    assert result.details == {"value": "abc"}


def test_injected_github_credentials_are_not_overridden_by_llm_args() -> None:
    seen: dict[str, Any] = {}

    def run(
        owner: str,
        repo: str,
        github_token: str | None = None,
        github_mode: str | None = None,
    ) -> dict[str, Any]:
        seen["github_token"] = github_token
        seen["github_mode"] = github_mode
        return {"ok": True}

    registered = RegisteredTool(
        name="github_probe",
        description="test github credential injection",
        input_schema={
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "github_token": {"type": "string"},
                "github_mode": {"type": "string"},
            },
            "required": ["owner", "repo"],
        },
        source="github",
        run=run,
        injected_params=("github_token", "github_mode"),
        extract_params=lambda sources: {
            "owner": sources["github"]["owner"],
            "repo": sources["github"]["repo"],
            "github_token": sources["github"]["auth_token"],
            "github_mode": sources["github"]["mode"],
        },
    )

    result = execute_tool_calls(
        [
            ToolCall(
                id="github-1",
                name="github_probe",
                input={
                    "owner": "wrong",
                    "repo": "wrong",
                    "github_token": "llm-token",
                    "github_mode": "metadata",
                },
            )
        ],
        [registered],
        {
            "github": {
                "connection_verified": True,
                "owner": "Tracer-Cloud",
                "repo": "opensre",
                "auth_token": "injected-token",
                "mode": "streamable-http",
            }
        },
    )[0]

    assert result.is_error is False
    assert seen == {
        "github_token": "injected-token",
        "github_mode": "streamable-http",
    }


def test_bookkeeping_may_accompany_one_action_in_provider_order() -> None:
    tools = [
        _tool("plan", role=ToolRole.BOOKKEEPING, execute=lambda _args, _ctx: {"order": 1}),
        _tool("work", execute=lambda _args, _ctx: {"order": 2}),
        _tool("memory", role=ToolRole.BOOKKEEPING, execute=lambda _args, _ctx: {"order": 3}),
    ]
    calls = [_call("plan", "x"), _call("work", "x"), _call("memory", "x")]

    results = execute_tool_calls(calls, tools, {})

    assert [result.details for result in results] == [{"order": 1}, {"order": 2}, {"order": 3}]
    assert not any(result.is_error for result in results)


def test_two_actions_in_one_response_execute_nothing() -> None:
    ran: list[str] = []

    def record(name: str) -> Any:
        def execute(_args: dict[str, Any], _ctx: AgentToolContext) -> dict[str, Any]:
            ran.append(name)
            return {"ok": True}

        return execute

    tools = [_tool("first", execute=record("first")), _tool("second", execute=record("second"))]
    batch_seen: list[int] = []
    hooks = ToolExecutionHooks(before_tool_batch=lambda calls: batch_seen.append(len(calls)))

    results = execute_tool_calls([_call("first"), _call("second")], tools, {}, hooks=hooks)

    assert ran == []
    assert batch_seen == []
    assert [result.is_error for result in results] == [True, True]
    assert len({result.content for result in results}) == 1
    assert "one action per response" in str(results[0].content)
    assert "first, second" in str(results[0].content)


def test_turn_ending_tool_must_be_alone_even_beside_bookkeeping() -> None:
    ran: list[str] = []

    def execute(_args: dict[str, Any], _ctx: AgentToolContext) -> dict[str, Any]:
        ran.append("ran")
        return {"ok": True}

    tools = [
        _tool("plan", role=ToolRole.BOOKKEEPING, execute=execute),
        _tool("menu", role=ToolRole.TURN_ENDING, execute=execute),
    ]

    results = execute_tool_calls([_call("plan"), _call("menu")], tools, {})

    assert ran == []
    assert all(result.is_error for result in results)
    assert "menu" in str(results[0].content)
    assert "only" in str(results[0].content)


def test_registered_tool_role_and_unknown_tool_count_as_actions() -> None:
    registered = RegisteredTool(
        name="registered_plan",
        description="test registered tool",
        input_schema=_schema(["value"]),
        source="knowledge",
        run=lambda value: {"value": value},
        role=ToolRole.BOOKKEEPING,
    )
    tool_map = {"registered_plan": registered, "work": _tool("work")}

    assert response_batch_violation([_call("registered_plan"), _call("work")], tool_map) is None
    assert response_batch_violation([_call("unknown_tool")], tool_map) is None
    assert response_batch_violation([_call("unknown_tool"), _call("work")], tool_map) is not None
    assert response_batch_violation([], tool_map) is None


class _FakeLLM:
    def __init__(self, responses: Iterator[AgentLLMResponse]) -> None:
        self._responses = responses
        self.seen_messages: list[list[dict[str, Any]]] = []
        self.model_id: str | None = None

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        return [{"name": tool.name} for tool in tools]

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = system
        _ = tools
        self.seen_messages.append(messages)
        return next(self._responses)

    def build_assistant_message(
        self,
        content: str,
        tool_calls: list[ToolCall],
    ) -> dict[str, Any]:
        return {"role": "assistant", "content": content, "tool_calls": [tc.id for tc in tool_calls]}

    def build_tool_result_message(
        self,
        tool_calls: list[ToolCall],
        results: list[Any],
    ) -> dict[str, Any]:
        return {"role": "tool", "results": list(zip([tc.id for tc in tool_calls], results))}


def test_tool_terminate_hint_stops_agent_loop() -> None:
    llm = _FakeLLM(iter([AgentLLMResponse(content="", tool_calls=[_call()], raw_content=None)]))
    tool = _tool(execute=lambda _args, _ctx: ToolExecutionResult(content="done", terminate=True))

    result = Agent(
        llm=llm,
        system="sys",
        tools=[tool],
        resolved_integrations={},
        max_iterations=3,
    ).run([{"role": "user", "content": "go"}])

    assert result.terminated_by_tool is True
    assert result.hit_iteration_cap is False
    assert len(result.tool_results) == 1


def test_provider_boundary_hooks_transform_convert_and_observe() -> None:
    requests: list[ProviderRequest] = []
    llm = _FakeLLM(iter([AgentLLMResponse(content="final", tool_calls=[], raw_content=None)]))

    hooks = ProviderHooks(
        transform_messages=lambda messages: list(messages)[-1:],
        convert_to_llm=lambda _llm, messages: [
            {"role": "user", "content": f"converted:{messages[0].content}"}
        ],
        before_provider_request=lambda request: requests.append(request) or request,
        after_provider_response=lambda _request, response: response,
        get_api_key=lambda env_name: f"fake:{env_name}",
    )

    result = Agent(
        llm=llm,
        system="sys",
        tools=[],
        resolved_integrations={},
        max_iterations=1,
        provider_hooks=hooks,
    ).run([{"role": "user", "content": "first"}, {"role": "user", "content": "second"}])

    assert result.final_text == "final"
    assert llm.seen_messages == [[{"role": "user", "content": "converted:second"}]]
    assert requests[0].messages == [{"role": "user", "content": "converted:second"}]
    assert hooks.get_api_key is not None
    assert hooks.get_api_key("OPENAI_API_KEY") == "fake:OPENAI_API_KEY"


def test_execute_tool_calls_emits_tool_span_without_tool_author_hooks(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Community tools stay untouched: spans come from the shared executor."""
    import json
    from pathlib import Path

    from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
    from infrastructure.observability.trace.spans import (
        NoopSessionTraceStore,
        bind_session_trace,
        set_session_trace_store,
    )
    from surfaces.interactive_shell.session.trace_store import JsonlSessionTraceStore

    monkeypatch.setattr(
        "core.agent_harness.session.persistence.jsonl_store.session_path",
        lambda session_id: Path(tmp_path) / f"{session_id}.jsonl",
    )
    storage = JsonlSessionStore()
    session_id = "sess-tool-exec"
    path = Path(tmp_path) / f"{session_id}.jsonl"
    path.write_text(
        json.dumps({"type": "session", "version": 2, "id": session_id}) + "\n",
        encoding="utf-8",
    )
    set_session_trace_store(JsonlSessionTraceStore(store=storage))
    try:
        with bind_session_trace(session_id):
            result = execute_tool_calls([_call()], [_tool()], {})[0]
        assert result.is_error is False
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").strip().splitlines()]
        tool_spans = [
            rec
            for rec in lines
            if rec.get("type") == "trace_span" and rec.get("span_kind") == "tool"
        ]
        assert len(tool_spans) == 1
        assert tool_spans[0]["name"] == "echo"
        assert tool_spans[0]["status"] == "ok"
        assert tool_spans[0]["attributes"]["outcome"] == "ok"
        assert tool_spans[0]["attributes"]["source"] == "agent"
        assert tool_spans[0]["attributes"]["tool_call_id"] == "echo-1"
    finally:
        set_session_trace_store(NoopSessionTraceStore())


def test_execute_tool_calls_span_marks_error_on_unknown_tool(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    from pathlib import Path

    from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
    from infrastructure.observability.trace.spans import (
        NoopSessionTraceStore,
        bind_session_trace,
        set_session_trace_store,
    )
    from surfaces.interactive_shell.session.trace_store import JsonlSessionTraceStore

    monkeypatch.setattr(
        "core.agent_harness.session.persistence.jsonl_store.session_path",
        lambda session_id: Path(tmp_path) / f"{session_id}.jsonl",
    )
    storage = JsonlSessionStore()
    session_id = "sess-tool-unknown"
    path = Path(tmp_path) / f"{session_id}.jsonl"
    path.write_text(
        json.dumps({"type": "session", "version": 2, "id": session_id}) + "\n",
        encoding="utf-8",
    )
    set_session_trace_store(JsonlSessionTraceStore(store=storage))
    try:
        with bind_session_trace(session_id):
            result = execute_tool_calls([_call("missing")], [], {})[0]
        assert result.is_error is True
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").strip().splitlines()]
        tool_spans = [
            rec
            for rec in lines
            if rec.get("type") == "trace_span" and rec.get("span_kind") == "tool"
        ]
        assert len(tool_spans) == 1
        assert tool_spans[0]["name"] == "missing"
        assert tool_spans[0]["status"] == "error"
        assert tool_spans[0]["attributes"]["outcome"] == "unknown_tool"
    finally:
        set_session_trace_store(NoopSessionTraceStore())


def test_execute_tool_calls_noop_sink_does_not_require_session_binding() -> None:
    """Prod default: tool execution must not depend on a registered trace sink."""
    from infrastructure.observability.trace.spans import (
        NoopSessionTraceStore,
        set_session_trace_store,
    )

    set_session_trace_store(NoopSessionTraceStore())
    result = execute_tool_calls([_call()], [_tool()], {})[0]
    assert result.is_error is False
    assert result.details == {"value": "ok"}


def _activate_tool_trace(tmp_path, monkeypatch: pytest.MonkeyPatch, session_id: str) -> Any:
    import json
    from pathlib import Path

    from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
    from infrastructure.observability.trace.spans import set_session_trace_store
    from surfaces.interactive_shell.session.trace_store import JsonlSessionTraceStore

    monkeypatch.setattr(
        "core.agent_harness.session.persistence.jsonl_store.session_path",
        lambda sid: Path(tmp_path) / f"{sid}.jsonl",
    )
    path = Path(tmp_path) / f"{session_id}.jsonl"
    path.write_text(
        json.dumps({"type": "session", "version": 2, "id": session_id}) + "\n",
        encoding="utf-8",
    )
    set_session_trace_store(JsonlSessionTraceStore(store=JsonlSessionStore()))
    return path


def _tool_spans_from(path: Any) -> list[dict[str, Any]]:
    import json

    return [
        rec
        for line in path.read_text(encoding="utf-8").strip().splitlines()
        for rec in [json.loads(line)]
        if rec.get("type") == "trace_span" and rec.get("span_kind") == "tool"
    ]


def test_execute_tool_calls_span_marks_validation_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from infrastructure.observability.trace.spans import (
        NoopSessionTraceStore,
        bind_session_trace,
        set_session_trace_store,
    )

    session_id = "sess-tool-validation"
    path = _activate_tool_trace(tmp_path, monkeypatch, session_id)
    try:
        with bind_session_trace(session_id):
            result = execute_tool_calls(
                [ToolCall(id="c1", name="echo", input={})],
                [_tool()],
                {},
            )[0]
        assert result.is_error is True
        spans = _tool_spans_from(path)
        assert len(spans) == 1
        assert spans[0]["status"] == "error"
        assert spans[0]["attributes"]["outcome"] == "validation_error"
    finally:
        set_session_trace_store(NoopSessionTraceStore())


def test_execute_tool_calls_span_marks_blocked(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from infrastructure.observability.trace.spans import (
        NoopSessionTraceStore,
        bind_session_trace,
        set_session_trace_store,
    )

    session_id = "sess-tool-blocked"
    path = _activate_tool_trace(tmp_path, monkeypatch, session_id)

    def before(_request: ToolExecutionRequest) -> BeforeToolCallResult:
        return BeforeToolCallResult(blocked=True, reason="policy deny")

    try:
        with bind_session_trace(session_id):
            result = execute_tool_calls(
                [_call()],
                [_tool()],
                {},
                hooks=ToolExecutionHooks(before_tool_call=before),
            )[0]
        assert result.is_error is True
        spans = _tool_spans_from(path)
        assert len(spans) == 1
        assert spans[0]["status"] == "error"
        assert spans[0]["attributes"]["outcome"] == "blocked"
        assert spans[0]["attributes"]["source"] == "agent"
    finally:
        set_session_trace_store(NoopSessionTraceStore())


def test_execute_tool_calls_span_marks_tool_error_and_exception(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from infrastructure.observability.trace.spans import (
        NoopSessionTraceStore,
        bind_session_trace,
        set_session_trace_store,
    )

    session_id = "sess-tool-err"
    path = _activate_tool_trace(tmp_path, monkeypatch, session_id)

    def soft_fail(_args: dict[str, Any], _ctx: AgentToolContext) -> dict[str, Any]:
        return {"error": "soft fail"}

    def hard_fail(_args: dict[str, Any], _ctx: AgentToolContext) -> dict[str, Any]:
        raise RuntimeError("hard fail")

    try:
        with bind_session_trace(session_id):
            soft = execute_tool_calls(
                [_call(name="soft")],
                [_tool(name="soft", execute=soft_fail)],
                {},
            )[0]
            hard = execute_tool_calls(
                [_call(name="hard")],
                [_tool(name="hard", execute=hard_fail)],
                {},
            )[0]
        assert soft.is_error is True
        assert hard.is_error is True
        spans = _tool_spans_from(path)
        by_name = {s["name"]: s for s in spans}
        assert by_name["soft"]["attributes"]["outcome"] == "tool_error"
        assert by_name["soft"]["status"] == "error"
        assert by_name["hard"]["attributes"]["outcome"] == "exception"
        assert by_name["hard"]["status"] == "error"
    finally:
        set_session_trace_store(NoopSessionTraceStore())
