"""Load one action-agent skill body on demand (thin harness / fat skills)."""

from __future__ import annotations

from typing import Any

from core.agent_harness.spi.grounding import (
    list_action_skills,
    load_skill_reference,
    skill_reference_names,
)
from core.agent_harness.tools import ActionToolScope, execute_with_action_context
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool, SideEffectLevel
from core.tool_framework.utils import object_schema, string_property
from tools.interactive_shell.action_names import ActionToolName
from tools.interactive_shell.actions.skill_entry import enter_skill


def _view_skill_reference(name: str, reference: str) -> dict[str, Any]:
    """Load one bundled reference file without re-entering the skill.

    Re-entering would re-run ``pre_execute`` menus and reset the active-skill
    tool scope, so a reference load never goes through :func:`enter_skill`.
    """
    content = load_skill_reference(name, reference)
    if not content:
        return {
            "ok": False,
            "name": name,
            "reference": reference,
            "error": f"unknown reference {reference!r} for skill {name!r}",
            "available_references": list(skill_reference_names(name)),
        }
    return {
        "ok": True,
        "name": name,
        "reference": reference,
        "summary": f"loaded the {reference} reference of {name}",
        "content": content,
    }


def execute_skill_view_tool(args: dict[str, Any], ctx: ActionToolScope) -> dict[str, Any]:
    name = str(args.get("name", "")).strip()
    if not name:
        available = [skill.name for skill in list_action_skills()]
        return {
            "ok": False,
            "error": "missing skill name",
            "available": available,
        }
    reference = str(args.get("reference", "")).strip()
    if reference:
        return _view_skill_reference(name, reference)
    return enter_skill(name, ctx, from_model=True)


def run_skill_view(*, name: str, reference: str = "", context: Any) -> dict[str, Any]:
    return execute_with_action_context(
        {"name": name, "reference": reference}, context, execute_skill_view_tool
    )


skill_view_tool = RegisteredTool(
    name=ActionToolName.SKILL_VIEW,
    description=(
        "Load the full body of one action-agent skill by name from the "
        "SKILLS INDEX. Call this in the same turn when the user request matches "
        "an indexed skill, then read the returned instructions before planning "
        "or executing its workflow. A "
        "skill may open its own menu on load; the result then tells you to end "
        "the turn. A skill that is already active does not need to be loaded "
        "again; its body is in your context. Pass reference to load one of the "
        "skill's linked reference files (named in its body as "
        "references/<name>.md) without re-entering the skill."
    ),
    input_schema=object_schema(
        properties={
            "name": string_property(
                description=(
                    "Skill name from the SKILLS INDEX (kebab-case), e.g. "
                    "'delivering-morning-briefings' or 'fixing-github-ci'."
                ),
                min_length=1,
            ),
            "reference": string_property(
                description=(
                    "Optional reference file stem linked from the skill body as "
                    "references/<stem>.md, e.g. 'metrics'. Loads that file only; "
                    "the skill is not re-entered."
                ),
            ),
        },
        required=("name",),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    accepts_runtime_context=True,
    run=run_skill_view,
    tags=("safe", "fast", "no-credentials"),
    side_effect_level=SideEffectLevel.READ_ONLY,
)


__all__ = ["execute_skill_view_tool", "run_skill_view", "skill_view_tool"]
