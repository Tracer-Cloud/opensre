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

_STARTED_NOTE = (
    "Claude Code is editing in a background task; its result is printed when the task "
    "finishes. The change is not done yet: do not mark a plan step complete for it and "
    "do not report it as finished until that result appears (/tasks shows progress)."
)


def execute_implementation_tool(args: dict[str, Any], ctx: ActionToolScope) -> dict[str, Any]:
    task = str(args.get("task", "")).strip()
    if not task:
        return {"ok": False, "error": "No implementation task was given."}
    launch = run_claude_code_implementation(task, require_subprocess_presenter(ctx))
    if not launch.started:
        return {"ok": False, "error": launch.detail}
    return {"ok": True, "task_id": launch.task_id, "status": "started", "note": _STARTED_NOTE}


def run_implementation(*, task: str, context: Any) -> dict[str, Any]:
    return execute_with_action_context({"task": task}, context, execute_implementation_tool)


code_implement_tool = RegisteredTool(
    name="code_implement",
    description=(
        "Run code implementation workflow using Claude Code in a background task. "
        "Not for git merge conflicts: use resolve_merge_conflicts for those."
    ),
    use_cases=[
        "User asks OpenSRE to implement a code change in the current repository",
    ],
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
