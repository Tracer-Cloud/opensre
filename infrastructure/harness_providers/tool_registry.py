"""Tool registry resolution.

Core resolves the tools for a surface without importing the tool packages:
``tools/harness_adapters.py`` installs the real sources once at boot, read
afterwards through ``resolve_*``. There is no setter — nothing rewrites the
sources after boot.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool

if TYPE_CHECKING:
    from core.tool import ToolRegistry


class _EmptyToolRegistry:
    """Default tool registry that resolves nothing until one is installed."""

    def tools_for_surface(self, _surface: ToolSurface) -> list[RegisteredTool]:
        return []

    def tool_map_for_surface(self, _surface: ToolSurface) -> dict[str, RegisteredTool]:
        return {}


_EMPTY_REGISTRY: ToolRegistry = _EmptyToolRegistry()


@dataclass(frozen=True)
class ToolSources:
    """The tool registry, installed once at boot."""

    registry: ToolRegistry = _EMPTY_REGISTRY
    skill_tools: Callable[[str], tuple[RegisteredTool, ...]] | None = None

    def install(self) -> None:
        """Bind these as the process-wide tool sources."""
        global _installed
        _installed = self


_installed: ToolSources | None = None


def resolve_surface_tools(surface: ToolSurface) -> list[RegisteredTool]:
    """Return the installed registry's tools for ``surface`` (empty before boot)."""
    return _installed.registry.tools_for_surface(surface) if _installed is not None else []


def resolve_surface_tool_map(surface: ToolSurface) -> dict[str, RegisteredTool]:
    """Return the installed registry's name→tool map for ``surface`` (empty before boot)."""
    return _installed.registry.tool_map_for_surface(surface) if _installed is not None else {}


def resolve_skill_tools(name: str) -> tuple[RegisteredTool, ...]:
    """Resolve bundled helpers without adding them to the global tool catalog."""
    if _installed is None or _installed.skill_tools is None:
        return ()
    return _installed.skill_tools(name)


def reset() -> None:
    """Clear the installed tool sources (tests)."""
    global _installed
    _installed = None


__all__ = [
    "ToolSources",
    "resolve_surface_tool_map",
    "resolve_surface_tools",
    "resolve_skill_tools",
]
