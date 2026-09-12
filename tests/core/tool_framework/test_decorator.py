"""Unit tests for core.tool_framework.tool_decorator (@tool)."""

from __future__ import annotations

from typing import Any

import pytest

from core.domain.types.tools import ToolRole
from core.tool.contracts import REGISTERED_TOOL_ATTR, BaseTool, RegisteredTool
from core.tool_framework.tool_decorator import tool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ABaseTool(BaseTool):
    name = "a_base_tool"
    description = "A simple base tool."
    input_schema = {"type": "object", "properties": {}}
    source = "grafana"

    def run(self) -> dict[str, Any]:
        return {"ok": True}


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------


def test_tool_applied_without_kwargs_to_function_is_noop() -> None:
    def plain_fn() -> None:
        pass

    result = tool(plain_fn)
    assert result is plain_fn
    assert not hasattr(plain_fn, REGISTERED_TOOL_ATTR)


def test_tool_applied_to_base_tool_instance_without_kwargs_is_noop() -> None:
    instance = _ABaseTool()
    result = tool(instance)
    assert result is instance
    assert not hasattr(instance, REGISTERED_TOOL_ATTR)


# ---------------------------------------------------------------------------
# Function registration
# ---------------------------------------------------------------------------


def test_tool_registers_function_with_explicit_metadata() -> None:
    @tool(
        name="my_fn_tool",
        description="Does something.",
        source="grafana",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
    )
    def my_fn(x: str) -> dict[str, Any]:
        return {"x": x}

    registered = getattr(my_fn, REGISTERED_TOOL_ATTR)
    assert isinstance(registered, RegisteredTool)
    assert registered.name == "my_fn_tool"
    assert registered.description == "Does something."
    assert registered.source == "grafana"


def test_tool_used_as_factory_produces_same_result() -> None:
    def inner_fn(x: str) -> dict[str, Any]:
        return {"x": x}

    decorator = tool(
        name="factory_tool",
        description="Factory style.",
        source="grafana",
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    result = decorator(inner_fn)
    registered = getattr(result, REGISTERED_TOOL_ATTR)
    assert registered.name == "factory_tool"


def test_function_tool_surfaces_defaults_to_chat() -> None:
    @tool(
        name="default_surface_tool",
        description="Check surface default.",
        source="grafana",
        input_schema={"type": "object", "properties": {}},
    )
    def fn() -> None:
        pass

    registered = getattr(fn, REGISTERED_TOOL_ATTR)
    assert registered.surfaces == ("chat",)


def test_function_tool_surfaces_are_propagated() -> None:
    @tool(
        name="multi_surface_tool",
        description="Appears in two surfaces.",
        source="grafana",
        input_schema={"type": "object", "properties": {}},
        surfaces=("action", "chat"),
    )
    def fn() -> None:
        pass

    registered = getattr(fn, REGISTERED_TOOL_ATTR)
    assert set(registered.surfaces) == {"action", "chat"}


def test_function_tool_with_source_none_raises() -> None:
    with pytest.raises((ValueError, TypeError)):
        tool(
            name="no_source",
            description="Missing source.",
            source=None,  # type: ignore[arg-type]
            input_schema={"type": "object", "properties": {}},
        )(lambda: None)


# ---------------------------------------------------------------------------
# BaseTool annotation cases
# ---------------------------------------------------------------------------


def test_tool_attaches_registered_tool_to_base_tool_when_surfaces_overridden() -> None:
    instance = _ABaseTool()
    result = tool(instance, surfaces=("chat",))
    assert result is instance
    registered = getattr(instance, REGISTERED_TOOL_ATTR)
    assert isinstance(registered, RegisteredTool)
    assert registered.surfaces == ("chat",)


def test_tool_attaches_registered_tool_when_tags_overridden() -> None:
    instance = _ABaseTool()
    tool(instance, tags=("beta",))
    registered = getattr(instance, REGISTERED_TOOL_ATTR)
    assert registered.tags == ("beta",)


def test_tool_attaches_registered_tool_when_requires_approval_overridden() -> None:
    instance = _ABaseTool()
    tool(instance, requires_approval=True, approval_reason="needs review")
    registered = getattr(instance, REGISTERED_TOOL_ATTR)
    assert registered.requires_approval is True
    assert registered.approval_reason == "needs review"


def test_tool_attaches_registered_tool_when_role_overridden() -> None:
    instance = _ABaseTool()
    tool(instance, role=ToolRole.BOOKKEEPING)
    registered = getattr(instance, REGISTERED_TOOL_ATTR)
    assert registered.role is ToolRole.BOOKKEEPING


def test_tool_attaches_evidence_mapper_to_base_tool() -> None:
    """``@tool(evidence_mapper=...)`` on a BaseTool must register the mapper."""

    def _map_ok(evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]) -> None:
        evidence["ok"] = output.get("ok")

    instance = _ABaseTool()
    result = tool(instance, evidence_mapper=_map_ok)
    assert result is instance
    registered = getattr(instance, REGISTERED_TOOL_ATTR)
    assert registered.evidence_mapper is _map_ok
    lifted: dict[str, Any] = {}
    registered.evidence_mapper(lifted, {"ok": True}, {})
    assert lifted == {"ok": True}


def test_tool_evidence_mapper_overrides_base_tool_class_mapper() -> None:
    class _MappedTool(_ABaseTool):
        @staticmethod
        def evidence_mapper(
            evidence: dict[str, Any], _output: dict[str, Any], _input: dict[str, Any]
        ) -> None:
            evidence["from_class"] = True

    def _from_decorator(
        evidence: dict[str, Any], _output: dict[str, Any], _input: dict[str, Any]
    ) -> None:
        evidence["from_decorator"] = True

    instance = _MappedTool()
    tool(instance, evidence_mapper=_from_decorator)
    registered = getattr(instance, REGISTERED_TOOL_ATTR)
    assert registered.evidence_mapper is _from_decorator


# ---------------------------------------------------------------------------
# display_name registration
# ---------------------------------------------------------------------------


def test_tool_registers_function_with_display_name_and_source() -> None:
    """@tool(display_name=..., source=...) must trigger registration."""

    @tool(display_name="Pretty Name", source="grafana")
    def display_name_source_fn() -> None:
        """Does something useful."""

    assert hasattr(display_name_source_fn, REGISTERED_TOOL_ATTR)
    registered = getattr(display_name_source_fn, REGISTERED_TOOL_ATTR)
    assert isinstance(registered, RegisteredTool)
    assert registered.display_name == "Pretty Name"


def test_tool_display_name_without_source_raises() -> None:
    """display_name alone cannot form a valid RegisteredTool — source is required."""
    with pytest.raises((ValueError, TypeError)):
        tool(display_name="Pretty Name")(lambda: None)
