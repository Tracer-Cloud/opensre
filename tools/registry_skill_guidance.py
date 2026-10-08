"""Attach SKILL.md workflow guidance to discovered tools.

The registry facade (:mod:`tools.registry`) calls :func:`apply_skill_guidance`
after collecting tools so a tool's description carries the workflow guidance the
matching SKILL.md declares for it.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from config.constants.paths import REPO_ROOT
from config.constants.skill_success import success_section
from core.tool import RegisteredTool
from core.tool_framework import format_tool_skill_guidance, load_tool_skill_guidance

logger = logging.getLogger(__name__)

_MAX_TOOL_SKILL_GUIDANCE_CHARS = 2400


def _skill_guidance_files() -> tuple[Path, ...]:
    """Return explicit and package-local SKILL.md files attached at registry load."""

    explicit = (
        REPO_ROOT / "integrations" / "github" / "tools" / "workflow" / "SKILL.md",
        REPO_ROOT
        / "integrations"
        / "sentry"
        / "tools"
        / "skills"
        / "summarizing-sentry-issues"
        / "SKILL.md",
        REPO_ROOT
        / "integrations"
        / "posthog"
        / "tools"
        / "skills"
        / "summarizing-posthog-analytics"
        / "SKILL.md",
        REPO_ROOT / "integrations" / "github" / "tools" / "github_cli" / "SKILL.md",
        REPO_ROOT / "integrations" / "github" / "tools" / "ci_fix" / "SKILL.md",
        REPO_ROOT / "integrations" / "github" / "tools" / "security_fix" / "SKILL.md",
        REPO_ROOT / "integrations" / "yandex_cloud" / "tools" / "SKILL.md",
    )
    discovered = sorted(
        (REPO_ROOT / "tools" / "system" / "python_execution_tool" / "skills").glob("*/SKILL.md")
    )
    return (*explicit, *discovered)


def tool_guidance_tools(name: str) -> tuple[str, ...]:
    """Tools whose descriptions carry the guidance called *name*; empty when none does."""
    wanted = name.strip().casefold()
    for skill_path in _skill_guidance_files():
        skill = load_tool_skill_guidance(skill_path).skill
        if skill is not None and skill.name.casefold() == wanted:
            return skill.tool_names
    return ()


def _truncate_skill_guidance(text: str, remaining: int) -> str:
    if len(text) <= remaining:
        return text
    return text[: remaining - 3].rstrip() + "..."


def _with_skill_guidance(tool: RegisteredTool, guidance: str) -> RegisteredTool:
    if not guidance:
        return tool
    return replace(
        tool,
        description=f"{tool.description}\n\nWorkflow guidance:\n{guidance}",
        skill_guidance=guidance,
    )


def apply_skill_guidance(
    tools_by_name: dict[str, RegisteredTool],
    *,
    known_tool_names: frozenset[str] | None = None,
) -> None:
    # Diagnostics validate against the full tool set (a surface load holds only a
    # subset); guidance still attaches only to tools present in ``tools_by_name``.
    diagnostic_names = (
        known_tool_names if known_tool_names is not None else frozenset(tools_by_name)
    )
    guidance_by_tool: dict[str, list[str]] = {}
    remaining_by_tool: dict[str, int] = {}

    for skill_path in _skill_guidance_files():
        result = load_tool_skill_guidance(skill_path, known_tool_names=diagnostic_names)
        for diagnostic in result.diagnostics:
            logger.warning(
                "[tools] Skill guidance %s (%s): %s",
                diagnostic.path,
                diagnostic.code,
                diagnostic.message,
            )
        skill = result.skill
        if skill is None or skill.disable_model_invocation:
            continue
        content = skill.content.strip()
        section = success_section(skill.name)
        suffix = f"\n\n{section}" if section and "## Success criteria" not in content else ""
        wrapper_size = len(format_tool_skill_guidance(replace(skill, content="")))
        for tool_name in skill.tool_names:
            if tool_name not in tools_by_name:
                continue
            remaining = remaining_by_tool.get(tool_name, _MAX_TOOL_SKILL_GUIDANCE_CHARS)
            separator_size = 2 if tool_name in guidance_by_tool else 0
            body_budget = remaining - separator_size - wrapper_size - len(suffix)
            if body_budget < 3:
                continue
            # Keep path metadata, success criteria and closing markup intact.
            bounded_content = _truncate_skill_guidance(content, body_budget) + suffix
            guidance = format_tool_skill_guidance(replace(skill, content=bounded_content))
            remaining_by_tool[tool_name] = remaining - separator_size - len(guidance)
            guidance_by_tool.setdefault(tool_name, []).append(guidance)

    for tool_name, guidances in guidance_by_tool.items():
        combined = "\n\n".join(guidances)
        tools_by_name[tool_name] = _with_skill_guidance(tools_by_name[tool_name], combined)
