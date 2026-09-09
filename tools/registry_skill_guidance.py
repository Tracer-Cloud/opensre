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


def _truncate_skill_guidance(text: str) -> str:
    if len(text) <= _MAX_TOOL_SKILL_GUIDANCE_CHARS:
        return text
    return text[: _MAX_TOOL_SKILL_GUIDANCE_CHARS - 3].rstrip() + "..."


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
        guidance = format_tool_skill_guidance(skill)
        for tool_name in skill.tool_names:
            if tool_name not in tools_by_name:
                continue
            guidance_by_tool.setdefault(tool_name, []).append(guidance)

    for tool_name, guidances in guidance_by_tool.items():
        combined = _truncate_skill_guidance("\n\n".join(guidances))
        tools_by_name[tool_name] = _with_skill_guidance(tools_by_name[tool_name], combined)
