"""Tests for single-file simple tools demonstrating the lightweight pattern."""

from __future__ import annotations

from collections.abc import Generator

import pytest

import app.tools.registry as registry_module
from app.tools.registered_tool import REGISTERED_TOOL_ATTR, RegisteredTool


@pytest.fixture(autouse=True)
def _reset_registry_cache() -> Generator[None, None, None]:
    """Reset the tool registry cache before and after each test."""
    registry_module.clear_tool_registry_cache()
    yield
    registry_module.clear_tool_registry_cache()


def test_simple_tools_are_discovered() -> None:
    """Verify that single-file function tools are auto-discovered."""
    tool_map = registry_module.get_registered_tool_map()

    assert "get_status" in tool_map, "get_status tool should be registered"
    assert "echo" in tool_map, "echo tool should be registered"


def test_get_status_tool_metadata() -> None:
    """Verify get_status tool has correct metadata."""
    tool_map = registry_module.get_registered_tool_map()
    get_status = tool_map["get_status"]

    assert get_status.name == "get_status"
    assert get_status.source == "knowledge"
    assert "system" in get_status.description.lower()
    assert "status" in get_status.description.lower()
    assert "detail_level" in get_status.input_schema["properties"]


def test_get_status_tool_execution() -> None:
    """Verify get_status tool executes correctly."""
    tool_map = registry_module.get_registered_tool_map()
    get_status = tool_map["get_status"]

    # Test basic detail level
    result = get_status.run(detail_level="basic")
    assert result["status"] == "operational"
    assert result["detail_level"] == "basic"
    assert "version" not in result  # Should not include full details

    # Test full detail level
    result = get_status.run(detail_level="full")
    assert result["status"] == "operational"
    assert result["detail_level"] == "full"
    assert result["version"] == "1.0.0"
    assert "components" in result

    # Test default parameter
    result = get_status.run()
    assert result["detail_level"] == "basic"


def test_echo_tool_metadata() -> None:
    """Verify echo tool has correct metadata."""
    tool_map = registry_module.get_registered_tool_map()
    echo = tool_map["echo"]

    assert echo.name == "echo"
    assert echo.source == "knowledge"
    assert "message" in echo.input_schema["properties"]
    assert "uppercase" in echo.input_schema["properties"]

    # Check required fields - uppercase is optional because it has a default value
    assert "message" in echo.input_schema["required"]
    assert "uppercase" not in echo.input_schema["required"]


def test_echo_tool_execution() -> None:
    """Verify echo tool executes correctly."""
    tool_map = registry_module.get_registered_tool_map()
    echo = tool_map["echo"]

    # Test basic echo
    result = echo.run(message="Hello")
    assert result["message"] == "Hello"
    assert result["original"] == "Hello"
    assert result["transformed"] is False

    # Test uppercase echo
    result = echo.run(message="Hello", uppercase=True)
    assert result["message"] == "HELLO"
    assert result["original"] == "Hello"
    assert result["transformed"] is True


def test_simple_tools_have_registered_tool_attribute() -> None:
    """Verify that simple tools have the REGISTERED_TOOL_ATTR attribute."""
    from app.tools import simple_tools

    # Check that the function has the registered tool attribute
    registered = getattr(simple_tools.get_status, REGISTERED_TOOL_ATTR, None)
    assert isinstance(registered, RegisteredTool)
    assert registered.name == "get_status"

    registered = getattr(simple_tools.echo, REGISTERED_TOOL_ATTR, None)
    assert isinstance(registered, RegisteredTool)
    assert registered.name == "echo"


def test_single_file_vs_directory_tools_equal_footing() -> None:
    """Verify single-file tools have the same runtime contract as directory-based tools."""
    tool_map = registry_module.get_registered_tool_map()

    # Get a single-file tool
    single_file_tool = tool_map["get_status"]

    # Get a directory-based tool (e.g., get_sre_guidance)
    dir_tool = tool_map.get("get_sre_guidance")
    if dir_tool is None:
        pytest.skip("get_sre_guidance not available for comparison")

    # Both should have the same interface
    assert hasattr(single_file_tool, "name")
    assert hasattr(single_file_tool, "description")
    assert hasattr(single_file_tool, "input_schema")
    assert hasattr(single_file_tool, "source")
    assert hasattr(single_file_tool, "run")
    assert hasattr(single_file_tool, "is_available")
    assert hasattr(single_file_tool, "extract_params")

    # Both should be callable
    assert callable(single_file_tool)
    assert callable(dir_tool)


def test_simple_tools_are_available_by_default() -> None:
    """Verify simple tools are available by default (no source requirements)."""
    tool_map = registry_module.get_registered_tool_map()

    get_status = tool_map["get_status"]
    echo = tool_map["echo"]

    # Both should be available with empty sources
    assert get_status.is_available({}) is True
    assert echo.is_available({}) is True


def test_simple_tools_extract_params_returns_empty() -> None:
    """Verify simple tools have default extract_params behavior."""
    tool_map = registry_module.get_registered_tool_map()

    get_status = tool_map["get_status"]
    echo = tool_map["echo"]

    # Both should return empty dict by default (no automatic param extraction)
    assert get_status.extract_params({}) == {}
    assert echo.extract_params({}) == {}
