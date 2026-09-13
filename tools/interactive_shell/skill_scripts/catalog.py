"""Build named tools from validated skill-local script declarations."""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.agent_harness.spi.grounding import list_action_skills
from core.agent_harness.tools import action_scope_from_agent_context
from core.tool import AgentToolContext, RegisteredTool, SideEffectLevel, ToolSurface
from tools.interactive_shell.skill_scripts.runner import run_skill_script


def _executor(skill_name: str, tool_name: str, path: Path) -> Callable[..., dict[str, Any]]:
    def execute(context: AgentToolContext, **arguments: Any) -> dict[str, Any]:
        return run_skill_script(
            skill_name=skill_name,
            tool_name=tool_name,
            script_path=str(path),
            arguments=arguments,
            scope=action_scope_from_agent_context(context),
        )

    return execute


@lru_cache(maxsize=32)
def registered_skill_tools(name: str) -> tuple[RegisteredTool, ...]:
    """Return helper tools for a known bundled skill; never publish them globally."""
    skill = next((item for item in list_action_skills() if item.name == name), None)
    if skill is None:
        return ()
    tools: list[RegisteredTool] = []
    for declaration in skill.script_tools:
        path = declaration.resolve(skill.path)

        tools.append(
            RegisteredTool(
                name=declaration.name,
                description=declaration.description,
                input_schema=declaration.input_schema,
                source="interactive_shell",
                run=_executor(name, declaration.name, path),
                surfaces=(ToolSurface.ACTION,),
                side_effect_level=SideEffectLevel.MUTATING,
                accepts_runtime_context=True,
            )
        )
    return tuple(tools)
