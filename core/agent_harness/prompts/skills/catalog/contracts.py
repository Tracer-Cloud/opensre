"""Validated workflow identity and entry-menu contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SkillToolCall:
    """One static entry-menu call declared by a skill."""

    tool: str
    args: Mapping[str, Any]


@dataclass(frozen=True)
class ActionSkill:
    """One validated, discoverable workflow."""

    name: str
    description: str
    path: Path
    recurring: bool = False
    getting_started: str | None = None
    demo_order: int | None = None
    pre_execute: tuple[SkillToolCall, ...] = ()
    includes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillCatalog:
    """Usable skills and validation failures, including cards excluded at runtime."""

    skills: tuple[ActionSkill, ...]
    diagnostics: tuple[str, ...]
