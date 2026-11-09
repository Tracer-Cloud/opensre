"""Unit tests for core.tool.contracts (BaseTool contract)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

import core.tool.contracts as tool_contracts
from core.domain.types.tools import ToolRole
from core.tool_framework.tool_decorator import tool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MinimalTool(tool_contracts.BaseTool):
    name = "minimal_tool"
    description = "A minimal tool for testing."
    input_schema = {"type": "object", "properties": {}}
    source = "grafana"

    def run(self) -> dict[str, Any]:
        return {"ok": True}


# ---------------------------------------------------------------------------
# __init_subclass__ validation
# ---------------------------------------------------------------------------


def test_base_tool_rejects_blank_name() -> None:
    with pytest.raises(ValidationError, match="name"):
        type(
            "BlankNameTool",
            (tool_contracts.BaseTool,),
            {
                "name": "   ",
                "description": "Valid description",
                "input_schema": {"type": "object", "properties": {}},
                "source": "grafana",
                "run": lambda _self, **_kwargs: {},
            },
        )


def test_base_tool_rejects_blank_description() -> None:
    with pytest.raises(ValidationError, match="description"):
        type(
            "BlankDescriptionTool",
            (tool_contracts.BaseTool,),
            {
                "name": "valid_tool",
                "description": "   ",
                "input_schema": {"type": "object", "properties": {}},
                "source": "grafana",
                "run": lambda _self, **_kwargs: {},
            },
        )


def test_init_subclass_normalises_and_writes_back_metadata() -> None:
    class _Normalised(tool_contracts.BaseTool):
        name = "  padded_name  "
        description = "  padded description  "
        input_schema = {"type": "object", "properties": {}}
        source = "grafana"

        def run(self) -> dict[str, Any]:
            return {}

    assert _Normalised.name == "padded_name"
    assert _Normalised.description == "padded description"


# ---------------------------------------------------------------------------
# metadata() classmethod
# ---------------------------------------------------------------------------


def test_metadata_classmethod_returns_tool_metadata() -> None:
    meta = _MinimalTool.metadata()
    assert isinstance(meta, tool_contracts.ToolMetadata)
    assert meta.name == "minimal_tool"
    assert meta.description == "A minimal tool for testing."


def test_registry_metadata_classmethod_returns_defaults() -> None:
    registry = _MinimalTool.registry_metadata()
    assert registry.surfaces == ("chat",)
    assert registry.tags == ()
    assert registry.role is ToolRole.ACTION


def test_init_subclass_normalizes_registry_metadata() -> None:
    class _RegistryTool(tool_contracts.BaseTool):
        name = "registry_tool"
        description = "Registry metadata tool."
        input_schema = {"type": "object", "properties": {}}
        source = "grafana"
        surfaces = ("chat", "action")
        tags = ("metrics", " fast ", "metrics")
        role = ToolRole.BOOKKEEPING

        def run(self) -> dict[str, Any]:
            return {}

    assert _RegistryTool.surfaces == ("chat", "action")
    assert _RegistryTool.tags == ("metrics", "fast")
    assert _RegistryTool.role is ToolRole.BOOKKEEPING


def test_init_subclass_rejects_invalid_surfaces() -> None:
    with pytest.raises(ValidationError):
        type(
            "InvalidSurfaceTool",
            (tool_contracts.BaseTool,),
            {
                "name": "invalid_surface_tool",
                "description": "Bad surfaces.",
                "input_schema": {"type": "object", "properties": {}},
                "source": "grafana",
                "surfaces": ("not-a-surface",),
                "run": lambda _self, **_kwargs: {},
            },
        )


def test_from_base_tool_reads_registry_metadata_from_class() -> None:
    class _ChatTool(tool_contracts.BaseTool):
        name = "chat_tool"
        description = "Chat-facing tool."
        input_schema = {"type": "object", "properties": {}}
        source = "grafana"
        surfaces = ("action", "chat")
        tags = ("safe",)
        role = ToolRole.TURN_ENDING

        def run(self) -> dict[str, Any]:
            return {}

    registered = tool_contracts.RegisteredTool.from_base_tool(_ChatTool())
    assert registered.surfaces == ("action", "chat")
    assert registered.tags == ("safe",)
    assert registered.role is ToolRole.TURN_ENDING


# ---------------------------------------------------------------------------
# Default is_available / extract_params
# ---------------------------------------------------------------------------


def test_default_is_available_returns_true() -> None:
    t = _MinimalTool()
    assert t.is_available({}) is True


def test_default_extract_params_returns_empty_dict() -> None:
    t = _MinimalTool()
    assert t.extract_params({}) == {}


# ---------------------------------------------------------------------------
# Exception capture via __call__ (telemetry path)
# ---------------------------------------------------------------------------


class _ExplodingTool(tool_contracts.BaseTool):
    name = "exploding_base_tool"
    description = "Tool that raises for telemetry coverage."
    input_schema = {"type": "object", "properties": {}}
    source = "grafana"

    def run(self) -> dict[str, Any]:
        raise RuntimeError("base boom")


def test_base_tool_exception_is_captured_with_tool_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[BaseException, dict[str, object]]] = []

    def report_stub(exc: BaseException, **kwargs: object) -> None:
        captured.append((exc, kwargs))

    monkeypatch.setattr(tool_contracts, "report_exception", report_stub)

    result = _ExplodingTool()()

    assert result == {"error": "base boom", "exception_type": "RuntimeError"}
    assert len(captured) == 1
    exc, kwargs = captured[0]
    assert isinstance(exc, RuntimeError)
    assert kwargs["tags"] == {
        "surface": "tool",
        "tool_name": "exploding_base_tool",
        "source": "grafana",
    }


# ---------------------------------------------------------------------------
# ClassVar defaults are isolated per subclass
# ---------------------------------------------------------------------------


def test_subclass_use_cases_do_not_bleed_into_base() -> None:
    """Mutating BaseTool.use_cases must not affect subclasses defined later."""

    class _ToolA(tool_contracts.BaseTool):
        name = "tool_a"
        description = "Tool A."
        input_schema = {"type": "object", "properties": {}}
        source = "grafana"
        use_cases = ("case_a",)

        def run(self) -> dict[str, Any]:
            return {}

    class _ToolB(tool_contracts.BaseTool):
        name = "tool_b"
        description = "Tool B."
        input_schema = {"type": "object", "properties": {}}
        source = "grafana"

        def run(self) -> dict[str, Any]:
            return {}

    assert _ToolA.use_cases == ("case_a",)
    # ToolB has no use_cases declared; it must not inherit ToolA's
    assert _ToolB.use_cases == ()


def test_base_tool_mutable_defaults_are_distinct_between_subclasses() -> None:
    """Each subclass gets its own copy of use_cases/examples/requires/outputs."""

    class _ToolX(tool_contracts.BaseTool):
        name = "tool_x"
        description = "Tool X."
        input_schema = {"type": "object", "properties": {}}
        source = "grafana"
        examples = ("ex1",)
        requires = ("req1",)

        def run(self) -> dict[str, Any]:
            return {}

    class _ToolY(tool_contracts.BaseTool):
        name = "tool_y"
        description = "Tool Y."
        input_schema = {"type": "object", "properties": {}}
        source = "grafana"

        def run(self) -> dict[str, Any]:
            return {}

    assert _ToolX.examples == ("ex1",)
    assert _ToolY.examples == ()
    assert _ToolX.requires == ("req1",)
    assert _ToolY.requires == ()


def test_decorated_function_tool_exception_is_captured_with_tool_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[BaseException, dict[str, object]]] = []

    def report_stub(exc: BaseException, **kwargs: object) -> None:
        captured.append((exc, kwargs))

    monkeypatch.setattr(tool_contracts, "report_exception", report_stub)

    @tool(
        name="decorated_failure",
        description="Function tool that raises for telemetry coverage.",
        input_schema={"type": "object", "properties": {}},
        source="grafana",
    )
    def decorated_failure() -> dict[str, Any]:
        raise ValueError("decorated boom")

    registered = getattr(decorated_failure, tool_contracts.REGISTERED_TOOL_ATTR)
    result = registered()

    assert result == {"error": "decorated boom", "exception_type": "ValueError"}
    assert len(captured) == 1
    exc, kwargs = captured[0]
    assert isinstance(exc, ValueError)
    assert kwargs["tags"] == {
        "surface": "tool",
        "tool_name": "decorated_failure",
        "source": "grafana",
    }
