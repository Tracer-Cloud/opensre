"""Validated registry/runtime metadata for ``BaseTool`` subclasses.

``BaseToolRegistryMetadata`` covers fields the tool registry uses to decide
where a tool is exposed and how it executes, separate from the planner/evidence
contract in ``ToolMetadata``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, cast, runtime_checkable

from pydantic import Field, field_validator

from config.strict_config import StrictConfigModel
from core.domain.types.tools import ToolRole, ToolSurface

_DEFAULT_SURFACES: tuple[ToolSurface, ...] = (ToolSurface.CHAT,)
_VALID_SURFACES = frozenset(ToolSurface)


def normalize_surfaces(surfaces: Iterable[str] | None) -> tuple[ToolSurface, ...]:
    """Normalize and validate tool surface names."""
    if surfaces is None:
        return _DEFAULT_SURFACES

    normalized: list[ToolSurface] = []
    for raw_surface in surfaces:
        surface = str(raw_surface).strip().lower()
        if surface not in _VALID_SURFACES:
            valid = ", ".join(sorted(_VALID_SURFACES))
            raise ValueError(f"Unsupported tool surface '{surface}'. Expected one of: {valid}.")
        typed_surface = ToolSurface(surface)
        if typed_surface not in normalized:
            normalized.append(typed_surface)

    return tuple(normalized) or _DEFAULT_SURFACES


def normalize_tags(tags: Iterable[str] | None) -> tuple[str, ...]:
    """Normalize optional planner tags into a deduplicated tuple."""
    if tags is None:
        return ()

    normalized: list[str] = []
    for raw_tag in tags:
        tag = str(raw_tag).strip()
        if tag and tag not in normalized:
            normalized.append(tag)
    return tuple(normalized)


class BaseToolRegistryMetadata(StrictConfigModel):
    """Registry/runtime metadata declared on ``BaseTool`` subclasses."""

    surfaces: tuple[ToolSurface, ...] = Field(default=_DEFAULT_SURFACES)
    tags: tuple[str, ...] = ()
    role: ToolRole = ToolRole.ACTION

    @field_validator("surfaces", mode="before")
    @classmethod
    def _coerce_surfaces(cls, value: object) -> tuple[ToolSurface, ...]:
        if value is None:
            return normalize_surfaces(None)
        if isinstance(value, str):
            return normalize_surfaces((value,))
        return normalize_surfaces(cast(Iterable[str], value))

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return normalize_tags((value,))
        return normalize_tags(cast(Iterable[str], value))


@runtime_checkable
class ToolRegistry(Protocol):
    """Resolves the registered tools available to a named surface."""

    def tools_for_surface(self, surface: ToolSurface) -> list[Any]:
        """Return the registered tools for ``surface``."""

    def tool_map_for_surface(self, surface: ToolSurface) -> dict[str, Any]:
        """Return the registered tools for ``surface`` keyed by tool name."""


__all__ = [
    "BaseToolRegistryMetadata",
    "ToolRegistry",
    "normalize_surfaces",
    "normalize_tags",
]
