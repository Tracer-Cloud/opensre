"""The tool contract: what a tool is, how it runs, and where it is registered."""

from core.tool.contracts import (
    REGISTERED_TOOL_ATTR,
    AgentToolContext,
    BaseTool,
    EvidenceType,
    RegisteredTool,
    RuntimeTool,
    SideEffectLevel,
    ToolRole,
    ToolSurface,
)
from core.tool.execution import (
    BeforeToolCallResult,
    ToolExecutionHooks,
    ToolExecutionRequest,
    ToolExecutionResult,
    availability_view,
    report_run_error,
)
from core.tool.registry import ToolRegistry, normalize_surfaces

__all__ = [
    "REGISTERED_TOOL_ATTR",
    "AgentToolContext",
    "BaseTool",
    "BeforeToolCallResult",
    "EvidenceType",
    "RegisteredTool",
    "RuntimeTool",
    "SideEffectLevel",
    "ToolExecutionHooks",
    "ToolExecutionRequest",
    "ToolExecutionResult",
    "ToolRegistry",
    "ToolRole",
    "ToolSurface",
    "availability_view",
    "normalize_surfaces",
    "report_run_error",
]
