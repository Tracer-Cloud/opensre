"""Progressively expose a large action-tool catalog to the model."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

from core.tool import RegisteredTool, SideEffectLevel, ToolRole, ToolSurface

_DISCOVERY_TOOL_NAME = "tool_search"
_MIN_CATALOG_SIZE = 16
_MAX_RESULTS = 8
_ALWAYS_VISIBLE = frozenset(
    {
        "cli_exec",
        "code_implement",
        "get_sre_guidance",
        "llm_set_provider",
        "skill_view",
        "shell_run",
        "slash_invoke",
        "task_cancel",
        "update_plan",
    }
)
_SKILL_CONTROL_TOOLS = frozenset({"ask_user_choice"})
_GOAL_CONTROL_TOOLS = frozenset({"session_goal_complete", "task_cancel"})
_SEARCH_ALIASES = {
    "eks": frozenset({"k8s", "kubernetes"}),
    "k8s": frozenset({"eks", "kubernetes"}),
    "kubernetes": frozenset({"eks", "k8s"}),
    "postgres": frozenset({"postgresql"}),
    "postgresql": frozenset({"postgres"}),
}


def _terms(value: str) -> frozenset[str]:
    raw = set(re.findall(r"[a-z0-9]+", value.casefold()))
    expanded = set(raw)
    for term in raw:
        expanded.update(_SEARCH_ALIASES.get(term, ()))
        if len(term) > 3 and term.endswith("ies"):
            expanded.add(f"{term[:-3]}y")
        elif len(term) > 3 and term.endswith("s"):
            expanded.add(term[:-1])
    return frozenset(expanded)


def _brief_description(tool: RegisteredTool) -> str:
    description = tool.description.split("\n\nWorkflow guidance:", 1)[0].strip()
    return description if len(description) <= 240 else f"{description[:237].rstrip()}..."


class ProgressiveToolCatalog:
    """Offer core controls first and activate specialist tools through search."""

    def __init__(
        self,
        session: Any,
        source: Callable[[], tuple[RegisteredTool, ...]],
        *,
        enabled: bool,
        base_names: Iterable[str] = (),
    ) -> None:
        self._session = session
        self._source = source
        self._enabled = enabled
        self._base_names = frozenset(base_names)
        self._activated: set[str] = set()
        self._last_key: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        self._snapshot: tuple[RegisteredTool, ...] = ()
        self._discovery_tool = RegisteredTool(
            name=_DISCOVERY_TOOL_NAME,
            description=(
                "Search and activate specialist tools that are not currently visible. "
                "Call this when the request needs a capability absent from the visible "
                "schemas (for example Kubernetes, cloud, observability, messaging, "
                "repository, memory, or task management). Query activates the best "
                "matches; names activates exact tools. Activated schemas appear on the "
                "next reasoning step, where you call the selected tool."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Capability or operation to find, in a few concrete words.",
                    },
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Exact tool names to activate when already known.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MAX_RESULTS,
                        "description": "Maximum query matches to activate (default 5).",
                    },
                },
                "additionalProperties": False,
            },
            source="knowledge",
            run=self._search,
            surfaces=(ToolSurface.ACTION,),
            tags=("safe", "fast", "no-credentials"),
            side_effect_level=SideEffectLevel.NONE,
            role=ToolRole.BOOKKEEPING,
        )

    def snapshot(self) -> tuple[RegisteredTool, ...]:
        """Return one stable visible snapshot until activation or skill state changes."""
        full = self._source()
        if not self._enabled or len(full) < _MIN_CATALOG_SIZE:
            return full
        full_names = tuple(tool.name for tool in full)
        visible_names = set(_ALWAYS_VISIBLE)
        visible_names.update(self._activated)
        if getattr(self._session, "active_skill", None):
            visible_names.update(_SKILL_CONTROL_TOOLS)
        if getattr(self._session, "session_goal", None) is not None:
            visible_names.update(_GOAL_CONTROL_TOOLS)
        key = (full_names, tuple(sorted(visible_names)))
        if key == self._last_key:
            return self._snapshot
        visible = [tool for tool in full if tool.name in visible_names]
        # Skill-local helpers are appended by the source and must remain immediately usable.
        visible.extend(
            tool
            for tool in full
            if tool.name not in self._base_names and tool.name not in visible_names
        )
        if any(tool.name == _DISCOVERY_TOOL_NAME for tool in full):
            raise ValueError(f"registered tool name {_DISCOVERY_TOOL_NAME!r} is reserved")
        hidden_exists = any(tool.name not in visible_names for tool in full)
        if hidden_exists:
            visible.append(self._discovery_tool)
        self._last_key = key
        self._snapshot = tuple(visible)
        return self._snapshot

    def _search(
        self,
        *,
        query: str = "",
        names: Iterable[str] | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        full = self._source()
        by_name = {tool.name: tool for tool in full}
        requested = [str(name).strip() for name in names or () if str(name).strip()]
        not_found = [name for name in requested if name not in by_name]
        matches = [by_name[name] for name in requested if name in by_name]
        clean_query = query.strip()
        if clean_query:
            matches.extend(self._query_matches(full, clean_query, limit=max(1, min(limit, 8))))
        unique = {tool.name: tool for tool in matches}
        self._activated.update(unique)
        visible_names = {tool.name for tool in self.snapshot()}
        return {
            "ok": bool(unique),
            "query": clean_query,
            "activated": [
                {"name": tool.name, "description": _brief_description(tool)}
                for tool in unique.values()
            ],
            "not_found": not_found,
            "remaining_hidden": sum(tool.name not in visible_names for tool in full),
            "summary": (
                "Activated tool schemas are available on the next reasoning step."
                if unique
                else "No tools matched. Search again with a provider, resource, or operation name."
            ),
        }

    @staticmethod
    def _query_matches(
        tools: tuple[RegisteredTool, ...], query: str, *, limit: int
    ) -> list[RegisteredTool]:
        query_terms = _terms(query)
        ranked: list[tuple[int, str, RegisteredTool]] = []
        for tool in tools:
            if tool.name == _DISCOVERY_TOOL_NAME:
                continue
            name_terms = _terms(tool.name)
            metadata = " ".join(
                (
                    tool.description.split("\n\nWorkflow guidance:", 1)[0],
                    str(tool.source),
                    " ".join(tool.tags),
                    " ".join(tool.use_cases),
                )
            )
            score = 8 * len(query_terms & name_terms) + len(query_terms & _terms(metadata))
            if query.casefold() in tool.name.casefold():
                score += 40
            if score:
                ranked.append((score, tool.name, tool))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [tool for _score, _name, tool in ranked[:limit]]


__all__ = ["ProgressiveToolCatalog"]
