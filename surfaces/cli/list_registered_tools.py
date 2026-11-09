"""Print registered tool names grouped by integration."""

from integrations.registry import INTEGRATION_SPECS
from tools.registry import get_registered_tools
from tools.registry_discovery import INTEGRATION_TOOL_PACKAGES


def get_integration_names() -> set[str]:
    """Return all known integration names."""
    names = {spec.service for spec in INTEGRATION_SPECS}

    names.update(package.split(".")[1] for package in INTEGRATION_TOOL_PACKAGES)

    return names


def get_integration_from_module(origin_module: str) -> str | None:
    """Return the integration owning an integration tool module."""
    parts = origin_module.split(".")

    if len(parts) >= 3 and parts[0] == "integrations" and parts[2] == "tools":
        return parts[1]

    return None


def get_registered_tools_by_integration() -> dict[str, list[str]]:
    """Group registered tool names by integration."""
    integration_names = sorted(get_integration_names())
    result: dict[str, list[str]] = {name: [] for name in integration_names}

    for tool in get_registered_tools():
        integration = get_integration_from_module(tool.origin_module)

        if integration is None:
            continue

        result[integration].append(tool.name)

    return {name: sorted(tool_names) for name, tool_names in sorted(result.items())}


def main() -> None:
    tools_by_integration = get_registered_tools_by_integration()

    for integration, tools in tools_by_integration.items():
        print(f"{integration}:")

        if not tools:
            print("  (no registered tools)")
            continue

        for tool in tools:
            print(f"  - {tool}")


if __name__ == "__main__":
    main()
