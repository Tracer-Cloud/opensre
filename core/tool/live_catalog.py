"""Optional per-run tool snapshots that change only at request boundaries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.tool.contracts import RuntimeTool

_RESOURCE_KEY = "live_tool_catalog"


@dataclass(frozen=True)
class LiveToolCatalog[ToolT: RuntimeTool]:
    """Return the same immutable snapshot until the available tools change."""

    snapshot: Callable[[], tuple[ToolT, ...]]

    def bind(self, resources: dict[str, Any]) -> None:
        """Attach this catalog to one run's resources."""
        resources[_RESOURCE_KEY] = self

    @classmethod
    def from_resources(cls, resources: dict[str, Any]) -> LiveToolCatalog[ToolT] | None:
        """Read a catalog attached by the host, if present."""
        catalog = resources.get(_RESOURCE_KEY)
        return catalog if isinstance(catalog, cls) else None
