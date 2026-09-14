"""Validated workflow identity and entry-menu contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.agent_harness.prompts.skills.catalog.script_tools import SkillScriptTool


@dataclass(frozen=True)
class SkillEntryMenu:
    """The single-choice menu the host opens on skill entry; catalog data, never frontmatter."""

    title: str
    options: tuple[str, ...]
    allow_custom: bool = False

    def tool_args(self) -> dict[str, Any]:
        """Return the ``ask_user_choice`` arguments that open this menu."""
        return {
            "title": self.title,
            "options": list(self.options),
            "allow_custom": self.allow_custom,
        }


@dataclass(frozen=True)
class ActionSkill:
    """One validated, discoverable workflow."""

    name: str
    description: str
    path: Path
    recurring: bool = False
    getting_started: str | None = None
    demo_order: int | None = None
    entry_menu: SkillEntryMenu | None = None
    includes: tuple[str, ...] = ()
    script_tools: tuple[SkillScriptTool, ...] = ()


@dataclass(frozen=True)
class SkillCatalog:
    """Usable skills and validation failures, including cards excluded at runtime."""

    skills: tuple[ActionSkill, ...]
    diagnostics: tuple[str, ...]
