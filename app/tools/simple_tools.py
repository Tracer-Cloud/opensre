"""Simple example tools demonstrating the lightweight single-file approach.

This module shows how to create tools with minimal boilerplate:
- One file
- One function with @tool decorator
- Auto-discovered by the registry
"""

from __future__ import annotations

from typing import Any

from app.tools.tool_decorator import tool


@tool(
    name="get_status",
    source="knowledge",
    description="Get system status information.",
    use_cases=[
        "Checking if the system is operational",
        "Getting basic health status",
    ],
    input_schema={
        "type": "object",
        "properties": {
            "detail_level": {
                "type": "string",
                "enum": ["basic", "full"],
                "description": "Level of detail to return",
            },
        },
        "required": [],
    },
)
def get_status(detail_level: str = "basic") -> dict[str, Any]:
    """Get system status information.

    Args:
        detail_level: Level of detail to return (basic or full)

    Returns:
        Dictionary containing status information
    """
    result: dict[str, Any] = {
        "status": "operational",
        "detail_level": detail_level,
    }

    if detail_level == "full":
        result["version"] = "1.0.0"
        result["components"] = ["api", "worker", "database"]

    return result


@tool(
    name="echo",
    source="knowledge",
    description="Echo back the input message.",
    use_cases=[
        "Testing tool connectivity",
        "Debugging parameter passing",
    ],
    input_schema={
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Message to echo back",
            },
            "uppercase": {
                "type": "boolean",
                "description": "Convert message to uppercase",
                "default": False,
            },
        },
        "required": ["message"],
    },
)
def echo(message: str, uppercase: bool = False) -> dict[str, Any]:
    """Echo back the input message.

    Args:
        message: Message to echo back
        uppercase: Convert message to uppercase

    Returns:
        Dictionary containing the echoed message
    """
    result = message.upper() if uppercase else message
    return {"message": result, "original": message, "uppercase": uppercase}
