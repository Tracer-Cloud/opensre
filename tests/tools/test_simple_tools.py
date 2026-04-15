"""Tests for single-file simple tools demonstrating the lightweight pattern.

These tests import directly from the simple_tools module rather than using
the registry, since simple_tools is excluded from production registration
to prevent hardcoded demo data from being used in investigations.
"""

from __future__ import annotations

# Import directly from module - not via registry (simple_tools is skipped in production)
from app.tools import simple_tools
from app.tools.registered_tool import REGISTERED_TOOL_ATTR, RegisteredTool


def test_simple_tools_excluded_from_registry() -> None:
    """Verify simple_tools are excluded from production registry."""
    from app.tools import registry as registry_module

    tool_map = registry_module.get_registered_tool_map()

    assert "get_status" not in tool_map, "Demo get_status should not be in production registry"
    assert "echo" not in tool_map, "Demo echo should not be in production registry"


def test_get_status_tool_metadata() -> None:
    """Verify get_status tool has correct metadata."""
    registered = getattr(simple_tools.get_status, REGISTERED_TOOL_ATTR, None)
    assert isinstance(registered, RegisteredTool)

    assert registered.name == "get_status"
    assert registered.source == "knowledge"
    assert "system" in registered.description.lower()
    assert "status" in registered.description.lower()
    assert "detail_level" in registered.input_schema["properties"]


def test_get_status_tool_execution() -> None:
    """Verify get_status tool executes correctly."""
    registered = getattr(simple_tools.get_status, REGISTERED_TOOL_ATTR, None)
    assert isinstance(registered, RegisteredTool)

    # Test basic detail level
    result = registered.run(detail_level="basic")
    assert result["status"] == "operational"
    assert result["detail_level"] == "basic"
    assert "version" not in result  # Should not include full details

    # Test full detail level
    result = registered.run(detail_level="full")
    assert result["status"] == "operational"
    assert result["detail_level"] == "full"
    assert result["version"] == "1.0.0"
    assert "components" in result

    # Test default parameter
    result = registered.run()
    assert result["detail_level"] == "basic"


def test_echo_tool_metadata() -> None:
    """Verify echo tool has correct metadata."""
    registered = getattr(simple_tools.echo, REGISTERED_TOOL_ATTR, None)
    assert isinstance(registered, RegisteredTool)

    assert registered.name == "echo"
    assert registered.source == "knowledge"
    assert "message" in registered.input_schema["properties"]
    assert "uppercase" in registered.input_schema["properties"]

    # Check required fields - uppercase is optional because it has a default value
    assert "message" in registered.input_schema["required"]
    assert "uppercase" not in registered.input_schema["required"]


def test_echo_tool_execution() -> None:
    """Verify echo tool executes correctly."""
    registered = getattr(simple_tools.echo, REGISTERED_TOOL_ATTR, None)
    assert isinstance(registered, RegisteredTool)

    # Test basic echo
    result = registered.run(message="Hello")
    assert result["message"] == "Hello"
    assert result["original"] == "Hello"
    assert result["uppercase"] is False

    # Test uppercase echo
    result = registered.run(message="Hello", uppercase=True)
    assert result["message"] == "HELLO"
    assert result["original"] == "Hello"
    assert result["uppercase"] is True


def test_simple_tools_have_registered_tool_attribute() -> None:
    """Verify that simple tools have the REGISTERED_TOOL_ATTR attribute."""
    # Check that the function has the registered tool attribute
    registered = getattr(simple_tools.get_status, REGISTERED_TOOL_ATTR, None)
    assert isinstance(registered, RegisteredTool)
    assert registered.name == "get_status"

    registered = getattr(simple_tools.echo, REGISTERED_TOOL_ATTR, None)
    assert isinstance(registered, RegisteredTool)
    assert registered.name == "echo"


def test_simple_tools_function_contract() -> None:
    """Verify single-file tools have the same runtime contract as directory-based tools."""
    registered = getattr(simple_tools.get_status, REGISTERED_TOOL_ATTR, None)
    assert isinstance(registered, RegisteredTool)

    # Should have the same interface as production tools
    assert hasattr(registered, "name")
    assert hasattr(registered, "description")
    assert hasattr(registered, "input_schema")
    assert hasattr(registered, "source")
    assert hasattr(registered, "run")
    assert hasattr(registered, "is_available")
    assert hasattr(registered, "extract_params")

    # Should be callable
    assert callable(registered)


def test_simple_tools_are_available_by_default() -> None:
    """Verify simple tools are available by default (no source requirements)."""
    get_status = getattr(simple_tools.get_status, REGISTERED_TOOL_ATTR, None)
    echo = getattr(simple_tools.echo, REGISTERED_TOOL_ATTR, None)

    assert isinstance(get_status, RegisteredTool)
    assert isinstance(echo, RegisteredTool)

    # Both should be available with empty sources
    assert get_status.is_available({}) is True
    assert echo.is_available({}) is True


def test_simple_tools_extract_params_returns_empty() -> None:
    """Verify simple tools have default extract_params behavior."""
    get_status = getattr(simple_tools.get_status, REGISTERED_TOOL_ATTR, None)
    echo = getattr(simple_tools.echo, REGISTERED_TOOL_ATTR, None)

    assert isinstance(get_status, RegisteredTool)
    assert isinstance(echo, RegisteredTool)

    # Both should return empty dict by default (no automatic param extraction)
    assert get_status.extract_params({}) == {}
    assert echo.extract_params({}) == {}


def test_tool_decorator_pattern_documentation() -> None:
    """Verify the @tool decorator pattern works as documented in PR."""
    # This test documents the intended usage pattern for #275
    registered = getattr(simple_tools.echo, REGISTERED_TOOL_ATTR, None)
    assert isinstance(registered, RegisteredTool)

    # Verify all the metadata can be accessed
    assert registered.name == "echo"
    assert registered.description
    assert registered.input_schema["type"] == "object"
    assert registered.source == "knowledge"
    assert "investigation" in registered.surfaces
