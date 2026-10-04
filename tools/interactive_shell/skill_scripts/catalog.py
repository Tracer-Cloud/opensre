"""Build named tools from validated skill-local script declarations."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.agent_harness.spi.grounding import ActionSkill
from core.agent_harness.spi.skill_releases import active_skill_catalog
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


_CACHE_LIMIT = 32
_cache: dict[tuple[str, str], tuple[RegisteredTool, ...]] = {}
_cache_lock = threading.Lock()


def registered_skill_tools(name: str) -> tuple[RegisteredTool, ...]:
    """Return helper tools for a skill in the active catalog; never publish them globally.

    Cached per catalog release, so a newly activated release resolves its own
    script files instead of reusing paths from the previous one.
    """
    snapshot = active_skill_catalog().current()
    key = (snapshot.id, name)
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    skill = snapshot.find(name)
    tools = _build_skill_tools(name, skill) if skill is not None and skill.name == name else ()
    with _cache_lock:
        if len(_cache) >= _CACHE_LIMIT:
            _cache.clear()
        _cache[key] = tools
    return tools


def _build_skill_tools(name: str, skill: ActionSkill) -> tuple[RegisteredTool, ...]:
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
