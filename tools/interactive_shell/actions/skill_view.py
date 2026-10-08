"""Load one action-agent skill body on demand (thin harness / fat skills)."""

from __future__ import annotations

import re
from collections.abc import Mapping
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
from tools.registry_skill_guidance import tool_guidance_tools


def _view_skill_reference(name: str, reference: str) -> dict[str, Any]:
    """Load one bundled reference file without re-entering the skill.

    Re-entering would reopen the entry menu and reset the active-skill tool
    scope, so a reference load never goes through :func:`enter_skill`.
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


def execute_skill_view_tool(
    args: dict[str, Any],
    ctx: ActionToolScope,
    *,
    resolved_integrations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    name = str(args.get("name", "")).strip()
    query = str(args.get("query", "")).strip()
    if query and not name:
        return _search_skills(query)
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
    if not any(skill.name == name for skill in list_action_skills()):
        guided_tools = tool_guidance_tools(name)
        if guided_tools:
            return _already_loaded_guidance(name, guided_tools)
    return enter_skill(name, ctx, from_model=True, resolved_integrations=resolved_integrations)


def _search_skills(query: str) -> dict[str, Any]:
    """Return a bounded ranked workflow list without loading any skill body."""
    terms = frozenset(re.findall(r"[a-z0-9]+", query.casefold()))
    ranked: list[tuple[int, str, str]] = []
    for skill in list_action_skills():
        name_terms = frozenset(skill.name.split("-"))
        description_terms = frozenset(re.findall(r"[a-z0-9]+", skill.description.casefold()))
        score = 4 * len(terms & name_terms) + len(terms & description_terms)
        if query.casefold() in skill.name.casefold():
            score += 20
        if score:
            ranked.append((score, skill.name, skill.description))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    matches = [
        {"name": name, "description": description} for _score, name, description in ranked[:5]
    ]
    return {
        "ok": True,
        "query": query,
        "matches": matches,
        "summary": (
            "Load the best matching workflow with skill_view(name=...)."
            if matches
            else "No matching workflow. Continue without loading a skill."
        ),
    }


def _already_loaded_guidance(name: str, guided_tools: tuple[str, ...]) -> dict[str, Any]:
    """Guidance attached to tool descriptions has nothing to open; say so without failing."""
    listed = ", ".join(guided_tools)
    return {
        "ok": True,
        "name": name,
        "already_loaded": True,
        "tools": list(guided_tools),
        "summary": f"{name} is tool guidance, already loaded",
        "content": (
            f"{name} is guidance attached to these tools: {listed}. There is no separate "
            "skill to open: call the tool that fits the request."
        ),
    }


def run_skill_view(
    *, name: str = "", query: str = "", reference: str = "", context: Any
) -> dict[str, Any]:
    # The prerequisite gate reads the integrations this turn's tools receive,
    # so it agrees with the tools it protects.
    resolved: Mapping[str, Any] | None = getattr(context, "resolved_integrations", None)

    def execute(args: dict[str, Any], ctx: ActionToolScope) -> dict[str, Any]:
        return execute_skill_view_tool(args, ctx, resolved_integrations=resolved)

    return execute_with_action_context(
        {"name": name, "query": query, "reference": reference}, context, execute
    )


skill_view_tool = RegisteredTool(
    name=ActionToolName.SKILL_VIEW,
    description=(
        "Load the full body of one action-agent skill by name from the "
        "workflow-name index. Search with query when the right workflow name is "
        "not obvious; search results do not load a body. Call this in the same "
        "turn when the user request matches a workflow, then read the returned instructions before planning "
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
                    "Exact workflow name from discovery (kebab-case), e.g. "
                    "'delivering-morning-briefings' or 'repair-github-ci'."
                ),
            ),
            "query": string_property(
                description=(
                    "Desired multi-step outcome to search for when the exact workflow "
                    "name is unknown. Search first, then call again with name."
                ),
            ),
            "reference": string_property(
                description=(
                    "Optional reference file stem linked from the skill body as "
                    "references/<stem>.md, e.g. 'metrics'. Loads that file only; "
                    "the skill is not re-entered."
                ),
            ),
        },
        required=(),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    accepts_runtime_context=True,
    run=run_skill_view,
    tags=("safe", "fast", "no-credentials"),
    side_effect_level=SideEffectLevel.READ_ONLY,
)


__all__ = ["execute_skill_view_tool", "run_skill_view", "skill_view_tool"]
