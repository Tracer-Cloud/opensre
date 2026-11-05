"""Registry entrypoint for the Neon Object Storage object listing tool."""

from __future__ import annotations

from integrations.neon.tools.neon_storage_objects_tool.tool import (
    NeonStorageObjectsTool,
    list_neon_storage_objects,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "NeonStorageObjectsTool", "list_neon_storage_objects"]
