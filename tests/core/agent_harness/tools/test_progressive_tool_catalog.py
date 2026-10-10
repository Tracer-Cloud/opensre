"""Large tool catalogs are disclosed on demand without losing live execution."""

from __future__ import annotations

from types import SimpleNamespace

from core.agent_harness.tools.progressive_tool_catalog import ProgressiveToolCatalog
from core.tool import RegisteredTool, SideEffectLevel


def _tool(name: str, description: str = "General purpose operation") -> RegisteredTool:
    return RegisteredTool(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": {}},
        source="knowledge",
        run=lambda: {"ok": True},
        side_effect_level=SideEffectLevel.NONE,
    )


def _large_catalog() -> tuple[RegisteredTool, ...]:
    named = (
        _tool("skill_view"),
        _tool("shell_run"),
        _tool("slash_invoke"),
        _tool("get_sre_guidance"),
        _tool("update_plan"),
        _tool("ask_user_choice"),
        _tool("session_goal_complete"),
        _tool("task_cancel"),
        _tool("cli_exec"),
        _tool("code_implement"),
        _tool("llm_set_provider"),
        _tool("get_eks_events", "Read Kubernetes warning events for incident RCA"),
        _tool("list_eks_pods", "Inspect pod health and restarts in an EKS namespace"),
    )
    filler = tuple(_tool(f"specialist_{index}") for index in range(10))
    return (*named, *filler)


def test_large_catalog_starts_small_and_search_activates_matching_tools() -> None:
    tools = _large_catalog()
    session = SimpleNamespace(active_skill=None, session_goal=None)
    catalog = ProgressiveToolCatalog(
        session,
        lambda: tools,
        enabled=True,
        base_names=(tool.name for tool in tools),
    )

    initial = catalog.snapshot()
    initial_names = {tool.name for tool in initial}

    assert initial_names == {
        "ask_user_choice",
        "cli_exec",
        "llm_set_provider",
        "shell_run",
        "skill_view",
        "slash_invoke",
        "task_cancel",
        "tool_search",
        "update_plan",
    }
    search = next(tool for tool in initial if tool.name == "tool_search")
    result = search(query="Kubernetes incident RCA")
    refreshed_names = {tool.name for tool in catalog.snapshot()}

    assert result.details["ok"] is True
    assert {item["name"] for item in result.details["matches"]} >= {
        "get_eks_events",
        "list_eks_pods",
    }
    assert {"get_eks_events", "list_eks_pods"} <= refreshed_names


def test_active_skill_and_goal_expose_only_their_control_tools() -> None:
    tools = _large_catalog()
    session = SimpleNamespace(active_skill="repair-github-ci", session_goal=object())
    catalog = ProgressiveToolCatalog(
        session,
        lambda: tools,
        enabled=True,
        base_names=(tool.name for tool in tools),
    )

    names = {tool.name for tool in catalog.snapshot()}

    assert {"ask_user_choice", "update_plan"} <= names
    assert {"session_goal_complete", "task_cancel"} <= names
    assert "get_eks_events" not in names


def test_small_catalog_uses_the_same_progressive_policy() -> None:
    tools = (_tool("one"), _tool("two"))
    catalog = ProgressiveToolCatalog(
        SimpleNamespace(active_skill=None, session_goal=None),
        lambda: tools,
        enabled=True,
        base_names=(tool.name for tool in tools),
    )

    initial = catalog.snapshot()
    assert [tool.name for tool in initial] == ["tool_search"]

    result = initial[0](names=["one"])

    assert result.details["state"] == "matched"
    assert [tool.name for tool in catalog.snapshot()] == ["tool_search", "one"]


def test_activating_every_tool_reports_nothing_remaining() -> None:
    tools = _large_catalog()
    catalog = ProgressiveToolCatalog(
        SimpleNamespace(active_skill=None, session_goal=None),
        lambda: tools,
        enabled=True,
        base_names=(tool.name for tool in tools),
    )
    search = next(tool for tool in catalog.snapshot() if tool.name == "tool_search")

    result = search(names=[tool.name for tool in tools])

    assert result.details["remaining_hidden"] == 0
    assert "tool_search" not in {tool.name for tool in catalog.snapshot()}


def test_visible_tool_search_expands_the_full_description_and_guidance() -> None:
    full = _tool("skill_view", "Full workflow guidance")
    full.compact_description = "Find a workflow"
    full.skill_guidance = "Use the exact workflow contract."
    tools = (full, _tool("specialist"))
    catalog = ProgressiveToolCatalog(
        SimpleNamespace(active_skill=None, session_goal=None),
        lambda: tools,
        enabled=True,
        base_names=(tool.name for tool in tools),
    )

    initial = catalog.snapshot()
    assert next(tool for tool in initial if tool.name == "skill_view").description == (
        "Find a workflow"
    )

    result = next(tool for tool in initial if tool.name == "tool_search")(names=["skill_view"])

    assert result.details["matches"][0]["guidance"] == "Use the exact workflow contract."
    assert next(tool for tool in catalog.snapshot() if tool.name == "skill_view").description == (
        "Full workflow guidance"
    )
