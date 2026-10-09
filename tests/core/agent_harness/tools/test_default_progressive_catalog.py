"""The real default provider and ReAct loop use the bounded progressive catalog."""

from __future__ import annotations

from io import StringIO
from typing import Any

from rich.console import Console

from config.constants.tool_discovery import (
    INITIAL_TOOL_CATALOG_LIMIT,
    INITIAL_TOOL_CATALOG_ORDER,
    INITIAL_TOOL_SCHEMA_TOKEN_LIMIT,
)
from core.agent import Agent
from core.agent_harness import SessionCore
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.structured_history import tool_items_from_run
from core.context_budget import system_and_tools_overhead
from core.llm.shared.tool_schema_normalize import build_openai_tool_specs
from core.llm.types import AgentLLMResponse, ToolCall


def _provider(session: SessionCore) -> DefaultToolProvider:
    return DefaultToolProvider(session, Console(file=StringIO(), force_terminal=False))


def test_default_provider_initial_catalog_stays_within_count_and_schema_budgets() -> None:
    session = SessionCore()
    tools = _provider(session).action_tools(
        confirm_fn=None,
        is_tty=False,
        resolved_integrations={},
    )
    names = [tool.name for tool in tools]
    ordered = [name for name in INITIAL_TOOL_CATALOG_ORDER if name in names]

    assert len(tools) <= INITIAL_TOOL_CATALOG_LIMIT
    assert names == ordered
    assert system_and_tools_overhead(None, build_openai_tool_specs(tools)) <= (
        INITIAL_TOOL_SCHEMA_TOKEN_LIMIT
    )
    assert all(
        tool.description == tool.compact_description
        for tool in tools
        if tool.compact_description is not None
    )


class _SearchThenFinishLLM:
    model_id = "progressive-catalog-test"

    def __init__(self) -> None:
        self.requests: list[list[dict[str, Any]]] = []

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        return build_openai_tool_specs(tools)

    def invoke(
        self,
        _messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = system
        schemas = list(tools or ())
        self.requests.append(schemas)
        names = [str(schema["function"]["name"]) for schema in schemas]
        if len(self.requests) == 1:
            assert "tool_search" in names
            assert "shell_run" not in names
            return AgentLLMResponse(
                content="",
                tool_calls=[
                    ToolCall(id="search-1", name="tool_search", input={"names": ["shell_run"]})
                ],
                stop_reason="tool_use",
            )
        assert "shell_run" in names
        return AgentLLMResponse(content="catalog expanded", stop_reason="stop")

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {"role": "assistant", "content": content, "tool_calls": tool_calls}

    @staticmethod
    def build_tool_result_message(tool_calls: list[ToolCall], results: list[Any]) -> dict[str, Any]:
        return {"role": "tool", "tool_calls": tool_calls, "results": results}


def test_real_provider_and_react_loop_expand_without_persisting_discovery_output() -> None:
    session = SessionCore()
    provider = _provider(session)
    tools = provider.action_tools(confirm_fn=None, is_tty=False, resolved_integrations={})
    llm = _SearchThenFinishLLM()
    agent = Agent(
        llm=llm,
        system="Answer the request.",
        tools=tools,
        resolved_integrations={},
        tool_resources=provider.tool_resources(),
        max_iterations=3,
    )

    result = agent.run([{"role": "user", "content": "run a shell command"}])

    assert result.final_text == "catalog expanded"
    assert len(llm.requests) == 2
    assert result.tool_results[0][1].model_only is True
    assert tool_items_from_run(result.messages, history_count=0) == ()


class _EmptySearchLoopLLM(_SearchThenFinishLLM):
    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = (messages, system)
        schemas = list(tools or ())
        self.requests.append(schemas)
        if schemas:
            attempt = len(self.requests)
            return AgentLLMResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id=f"search-{attempt}",
                        name="tool_search",
                        input={"query": f"zzzxqvnoresult{attempt}"},
                    )
                ],
                stop_reason="tool_use",
            )
        return AgentLLMResponse(content="No suitable tool was available.", stop_reason="stop")


def test_three_reworded_empty_discovery_iterations_finalize_the_real_loop() -> None:
    session = SessionCore()
    provider = _provider(session)
    tools = provider.action_tools(confirm_fn=None, is_tty=False, resolved_integrations={})
    llm = _EmptySearchLoopLLM()
    agent = Agent(
        llm=llm,
        system="Answer the request.",
        tools=tools,
        resolved_integrations={},
        tool_resources=provider.tool_resources(),
        max_iterations=8,
    )

    result = agent.run([{"role": "user", "content": "use an impossible capability"}])

    assert result.stop_reason == "discovery_stagnation"
    assert result.final_text == "No suitable tool was available."
    assert len(llm.requests) == 4
    assert llm.requests[-1] == []
