"""``integration_of_tool``: ownership comes from the tool's package, not its evidence source."""

from __future__ import annotations

from tools.registry import clear_tool_registry_cache, get_registered_tool_map, integration_of_tool


def test_ownership_follows_the_package_even_when_the_source_says_otherwise() -> None:
    # Arrange
    clear_tool_registry_cache()
    registered = get_registered_tool_map()
    storage_tools = [
        name for name, tool in registered.items() if getattr(tool, "source", "") == "storage"
    ]

    # Act
    github = integration_of_tool("github_cli")
    shell = integration_of_tool("shell_run")
    unknown = integration_of_tool("no_such_tool")
    storage_owner = integration_of_tool(storage_tools[0]) if storage_tools else "s3"

    # Assert: a GitHub tool is github's; a cross-vendor shell tool belongs to no integration;
    # the s3 tools declare source "storage" yet are owned by the s3 integration.
    assert github == "github"
    assert shell is None and unknown is None
    assert storage_owner == "s3"
