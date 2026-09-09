"""Load one action-agent skill body on demand (thin harness / fat skills)."""

from __future__ import annotations

from typing import Any

from core.agent_harness.spi.grounding import list_action_skills
from core.agent_harness.tools import ActionToolScope, execute_with_action_context
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool, SideEffectLevel
from core.tool_framework.utils import object_schema, string_property
from tools.interactive_shell.action_names import ActionToolName
from tools.interactive_shell.actions.skill_entry import enter_skill


def execute_skill_view_tool(args: dict[str, Any], ctx: ActionToolScope) -> dict[str, Any]:
    name = str(args.get("name", "")).strip()
    if not name:
        available = [skill.name for skill in list_action_skills()]
        return {
            "ok": False,
            "error": "missing skill name",
            "available": available,
        }
    return enter_skill(name, ctx)


def run_skill_view(*, name: str, context: Any) -> dict[str, Any]:
    return execute_with_action_context({"name": name}, context, execute_skill_view_tool)


skill_view_tool = RegisteredTool(
    name=ActionToolName.SKILL_VIEW,
    description=(
        "Load the full body of one action-agent skill by name from the "
        "SKILLS INDEX. Call this in the same turn when the user request matches "
        "an indexed skill, BEFORE emitting that skill's tool sequence. Do not "
        "invent workflow steps from the one-line index description alone. A "
        "skill may open its own menu on load; the result then tells you to end "
        "the turn."
    ),
    input_schema=object_schema(
        properties={
            "name": string_property(
                description=(
                    "Skill name from the SKILLS INDEX (kebab-case), e.g. "
                    "'morning-report' or 'architecture-audit'."
                ),
                min_length=1,
            ),
        },
        required=("name",),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    parallel_safe=True,
    accepts_runtime_context=True,
    run=run_skill_view,
    tags=("safe", "fast", "no-credentials"),
    side_effect_level=SideEffectLevel.READ_ONLY,
)


__all__ = ["execute_skill_view_tool", "run_skill_view", "skill_view_tool"]
