"""Session-local availability of the active workflow's helper tools."""

from __future__ import annotations

from typing import Any

from core.agent_harness.tools.tool_context import capability_not_explicitly_disabled
from core.tool import RegisteredTool
from infrastructure.harness_providers import resolve_skill_tools


class SkillToolCatalog:
    """Cache one tool snapshot per active skill without changing global registration."""

    def __init__(self, session: Any, base: list[Any], *, enabled: bool) -> None:
        self._session = session
        self._base = tuple(base)
        self._base_names = {tool.name for tool in base}
        self._enabled = enabled
        self._active: str | None = None
        self._snapshot = self._base

    def snapshot(self) -> tuple[RegisteredTool, ...]:
        active = getattr(self._session, "active_skill", None)
        if (
            not self._enabled
            or not getattr(self._session, "skill_discovery_enabled", True)
            or not capability_not_explicitly_disabled(self._session, "shell_commands")
        ):
            active = None
        if active == self._active:
            return self._snapshot
        helpers = resolve_skill_tools(active) if isinstance(active, str) else ()
        if any(tool.name in self._base_names for tool in helpers):
            raise ValueError("skill script tools cannot shadow existing tools")
        self._active = active
        self._snapshot = (*self._base, *helpers)
        return self._snapshot
