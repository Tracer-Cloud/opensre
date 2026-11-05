"""Registry entrypoint for the Neon Object Storage buckets tool."""

from __future__ import annotations

from integrations.neon.tools.neon_storage_buckets_tool.tool import (
    NeonStorageBucketsTool,
    get_neon_storage_buckets,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "NeonStorageBucketsTool", "get_neon_storage_buckets"]
