"""Unit tests for core.tool.contracts (RegisteredTool contract)."""

from __future__ import annotations

from typing import Any

import pytest

from core.domain.types.evidence import EvidenceSource
from core.domain.types.tools import ToolRole, ToolSurface
from core.tool.contracts import BaseTool, RegisteredTool, _normalize_surfaces
from core.tool_framework.tool_decorator import tool

# ---------------------------------------------------------------------------
# Fixture tools
# ---------------------------------------------------------------------------


class _ReadOnlyTool(BaseTool):
    name = "read_only_tool"
    description = "A safe read-only tool"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    source: EvidenceSource = "storage"

    def run(self) -> dict[str, Any]:
        return {"status": "ok"}

    @classmethod
    def is_available(cls, sources: dict[str, dict]) -> bool:  # noqa: ARG003
        return True

    @classmethod
    def extract_params(cls, sources: dict[str, dict]) -> dict[str, Any]:  # noqa: ARG003
        return {}


class _DestructiveTool(BaseTool):
    name = "destructive_tool"
    description = "A tool that writes to external systems"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    source: EvidenceSource = "github"
    requires_approval = True
    approval_reason = "This tool modifies external resources"

    def run(self) -> dict[str, Any]:
        return {"status": "modified"}

    @classmethod
    def is_available(cls, sources: dict[str, dict]) -> bool:  # noqa: ARG003
        return True

    @classmethod
    def extract_params(cls, sources: dict[str, dict]) -> dict[str, Any]:  # noqa: ARG003
        return {}


@tool(
    name="approval_function_tool",
    source="github",
    description="Function tool requiring approval",
    input_schema={"type": "object", "properties": {}},
    requires_approval=True,
    approval_reason="Needs approval",
    approval_expiry_seconds=60,
)
def approval_function_tool() -> dict[str, Any]:
    return {"ok": True}


# ---------------------------------------------------------------------------
# requires_approval on BaseTool
# ---------------------------------------------------------------------------


class TestRequiresApprovalOnBaseTool:
    def test_default_requires_approval_is_false(self) -> None:
        assert _ReadOnlyTool().requires_approval is False
        assert _ReadOnlyTool().approval_reason == ""

    def test_requires_approval_set_to_true(self) -> None:
        assert _DestructiveTool().requires_approval is True
        assert _DestructiveTool().approval_reason == "This tool modifies external resources"


# ---------------------------------------------------------------------------
# requires_approval propagated through RegisteredTool
# ---------------------------------------------------------------------------


class TestRequiresApprovalOnRegisteredTool:
    def test_from_base_tool_carries_requires_approval(self) -> None:
        registered = RegisteredTool.from_base_tool(_DestructiveTool())
        assert registered.requires_approval is True
        assert registered.approval_reason == "This tool modifies external resources"

    def test_from_base_tool_default_no_approval(self) -> None:
        registered = RegisteredTool.from_base_tool(_ReadOnlyTool())
        assert registered.requires_approval is False
        assert registered.approval_reason == ""

    def test_from_base_tool_reads_role_default(self) -> None:
        registered = RegisteredTool.from_base_tool(_ReadOnlyTool())
        assert registered.role is ToolRole.ACTION

    def test_from_function_carries_requires_approval_metadata(self) -> None:
        registered = approval_function_tool.__opensre_registered_tool__  # type: ignore[attr-defined]
        assert registered.requires_approval is True
        assert registered.approval_reason == "Needs approval"
        assert registered.approval_expiry_seconds == 60


# ---------------------------------------------------------------------------
# public_input_schema — injected params stripped
# ---------------------------------------------------------------------------


def test_public_input_schema_strips_injected_params() -> None:
    def run(query: str, token: str) -> dict[str, Any]:
        return {}

    rt = RegisteredTool(
        name="inj_tool",
        description="Tool with injected params",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["query", "token"],
        },
        source="grafana",
        run=run,
        injected_params=("token",),
    )

    public = rt.public_input_schema
    assert "query" in public["properties"]
    assert "token" not in public["properties"]
    assert "query" in public["required"]
    assert "token" not in public["required"]


def test_public_input_schema_empty_injected_unchanged() -> None:
    def run(x: str) -> dict[str, Any]:
        return {}

    rt = RegisteredTool(
        name="plain_tool",
        description="No injected params",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        source="grafana",
        run=run,
    )
    assert rt.public_input_schema == rt.input_schema


def test_public_input_schema_is_cached_and_isolated_from_source() -> None:
    def run(query: str, token: str) -> dict[str, Any]:
        return {}

    rt = RegisteredTool(
        name="cached_tool",
        description="Tool whose public schema is computed once",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}, "token": {"type": "string"}},
            "required": ["query", "token"],
        },
        source="grafana",
        run=run,
        injected_params=("token",),
    )

    # Each access returns a fresh top-level dict, so mutating one caller's copy
    # cannot corrupt the shared cache seen by the next caller.
    first = rt.public_input_schema
    second = rt.public_input_schema
    assert first == second
    assert first is not second
    first.pop("required", None)
    assert "required" in second
    # And the cache did not mutate the source schema (injected param still present there).
    assert "token" in rt.input_schema["properties"]
    assert "token" not in second["properties"]


# ---------------------------------------------------------------------------
# validate_public_input
# ---------------------------------------------------------------------------


def _make_strict_tool() -> RegisteredTool:
    def run(name: str, count: int) -> dict[str, Any]:
        return {}

    return RegisteredTool(
        name="strict_tool",
        description="Strict input validation tool",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        source="grafana",
        run=run,
    )


def test_validate_public_input_valid_payload_returns_none() -> None:
    rt = _make_strict_tool()
    assert rt.validate_public_input({"name": "alice", "count": 5}) is None


def test_validate_public_input_missing_required_returns_error() -> None:
    rt = _make_strict_tool()
    error = rt.validate_public_input({})
    assert error is not None
    assert "missing required args" in error
    assert "name" in error


def test_validate_public_input_extra_arg_rejected_with_additional_properties_false() -> None:
    rt = _make_strict_tool()
    error = rt.validate_public_input({"name": "alice", "unexpected": "val"})
    assert error is not None
    assert "unexpected" in error


def test_validate_public_input_type_mismatch_returns_error() -> None:
    rt = _make_strict_tool()
    error = rt.validate_public_input({"name": "alice", "count": "not-an-int"})
    assert error is not None
    assert "count" in error


# ---------------------------------------------------------------------------
# from_function edge cases
# ---------------------------------------------------------------------------


def test_from_function_source_none_raises() -> None:
    with pytest.raises(ValueError, match="source"):
        RegisteredTool.from_function(lambda: None, source=None)  # type: ignore[arg-type]


def test_from_function_infers_description_from_docstring() -> None:
    def my_fn() -> None:
        """This is the docstring description."""

    rt = RegisteredTool.from_function(my_fn, source="grafana")
    assert rt.description == "This is the docstring description."


def test_from_function_uses_func_name_when_no_docstring() -> None:
    def snake_case_fn() -> None:
        pass

    rt = RegisteredTool.from_function(snake_case_fn, source="grafana")
    assert rt.description == "snake case fn"


def test_from_function_infers_input_schema_when_none_provided() -> None:
    def my_fn(host: str, port: int) -> None:
        pass

    rt = RegisteredTool.from_function(my_fn, source="grafana")
    assert "host" in rt.input_schema["properties"]
    assert "port" in rt.input_schema["properties"]


def test_from_function_explicit_description_overrides_docstring() -> None:
    def fn() -> None:
        """Docstring."""

    rt = RegisteredTool.from_function(fn, source="grafana", description="Explicit description")
    assert rt.description == "Explicit description"


# ---------------------------------------------------------------------------
# _normalize_surfaces
# ---------------------------------------------------------------------------


def test_normalize_surfaces_none_returns_chat_default() -> None:
    assert _normalize_surfaces(None) == (ToolSurface.CHAT,)


def test_normalize_surfaces_valid_list() -> None:
    result = _normalize_surfaces([ToolSurface.ACTION, ToolSurface.CHAT])
    assert set(result) == {ToolSurface.ACTION, ToolSurface.CHAT}


def test_normalize_surfaces_deduplicates() -> None:
    result = _normalize_surfaces([ToolSurface.ACTION, ToolSurface.ACTION])
    assert result == (ToolSurface.ACTION,)


def test_normalize_surfaces_invalid_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported tool surface"):
        _normalize_surfaces(["unknown_surface"])


def test_normalize_surfaces_empty_list_returns_chat_default() -> None:
    result = _normalize_surfaces([])
    assert result == (ToolSurface.CHAT,)


class _TaggedBaseTool(BaseTool):
    name = "tagged_base_tool"
    description = "Base tool with registry metadata."
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    source: EvidenceSource = "grafana"
    surfaces = (ToolSurface.ACTION,)
    tags = ("logs", "observability")
    role = ToolRole.BOOKKEEPING

    def run(self) -> dict[str, Any]:
        return {}


def test_from_base_tool_uses_class_registry_metadata() -> None:
    registered = RegisteredTool.from_base_tool(_TaggedBaseTool())
    assert registered.surfaces == ("action",)
    assert registered.tags == ("logs", "observability")
    assert registered.role is ToolRole.BOOKKEEPING


def test_from_base_tool_explicit_surfaces_override_class_metadata() -> None:
    registered = RegisteredTool.from_base_tool(_TaggedBaseTool(), surfaces=("chat",))
    assert registered.surfaces == ("chat",)
    assert registered.tags == ("logs", "observability")


# ---------------------------------------------------------------------------
# approval_expiry_seconds edge cases
# ---------------------------------------------------------------------------


def test_from_function_approval_expiry_zero_is_preserved() -> None:
    """approval_expiry_seconds=0 must not be coerced to the default by an 'or' fallback."""

    def fn() -> None:
        pass

    rt = RegisteredTool.from_function(
        fn,
        source="grafana",
        requires_approval=True,
        approval_reason="Expires immediately",
        approval_expiry_seconds=0,
    )
    assert rt.approval_expiry_seconds == 0


def test_from_function_approval_expiry_none_uses_default() -> None:
    """approval_expiry_seconds=None must fall back to DEFAULT_APPROVAL_EXPIRY_SECONDS."""
    from config.constants.tooling import DEFAULT_APPROVAL_EXPIRY_SECONDS

    def fn() -> None:
        pass

    rt = RegisteredTool.from_function(fn, source="grafana", approval_expiry_seconds=None)
    assert rt.approval_expiry_seconds == DEFAULT_APPROVAL_EXPIRY_SECONDS
