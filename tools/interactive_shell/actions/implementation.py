"""Implementation tool."""

from __future__ import annotations

from typing import Any

from core.agent_harness.tools import (
    ActionToolScope,
    capability_available_from_sources,
    execute_with_action_context,
)
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool, SideEffectLevel
from core.tool_framework.utils import object_schema, string_property
from tools.interactive_shell.implementation.claude_code_executor import (
    run_claude_code_implementation,
)
from tools.interactive_shell.subprocess import require_subprocess_presenter


def execute_implementation_tool(args: dict[str, Any], ctx: ActionToolScope) -> bool:
    task = str(args.get("task", "")).strip()
    if not task:
        return False
    run_claude_code_implementation(task, require_subprocess_presenter(ctx))
    return True


def run_implementation(*, task: str, context: Any) -> dict[str, Any]:
    return execute_with_action_context({"task": task}, context, execute_implementation_tool)


code_implement_tool = RegisteredTool(
    name="code_implement",
    description="Run code implementation workflow using Claude Code.",
    input_schema=object_schema(
        properties={
            "task": string_property(
                description="Implementation task to execute in the codebase.",
                min_length=1,
            )
        },
        required=("task",),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    accepts_runtime_context=True,
    run=run_implementation,
    is_available=lambda sources: capability_available_from_sources(sources, "implementation"),
)


__all__ = ["code_implement_tool", "execute_implementation_tool"]
