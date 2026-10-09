"""Session-local progressive disclosure for the action-tool catalog."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any

from config.constants.tool_discovery import (
    DISCOVERY_DETAIL_KEYS_KEY,
    DISCOVERY_PROGRESS_KEY,
    INITIAL_TOOL_CATALOG_LIMIT,
    INITIAL_TOOL_CATALOG_ORDER,
    INITIAL_TOOL_SCHEMA_TOKEN_LIMIT,
    MODEL_ONLY_PRESENTATION_KEY,
    SESSION_GOAL_CONTROL_TOOL_NAMES,
    TOOL_DISCOVERY_STATE_MATCHED,
    TOOL_DISCOVERY_STATE_NO_MATCH,
    TOOL_SEARCH_MAX_RESULTS,
    TOOL_SEARCH_NAME,
)
from core.context_budget import system_and_tools_overhead
from core.llm.shared.tool_schema_normalize import build_openai_tool_specs
from core.tool import (
    RegisteredTool,
    SideEffectLevel,
    ToolExecutionResult,
    ToolRole,
    ToolSurface,
)
from infrastructure.observability.operations_log import record_operation

_DYNAMIC_INSERT_AFTER = "update_plan"
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


def _fallback_compact_description(tool: RegisteredTool) -> str:
    description = tool.description.split("\n\nWorkflow guidance:", 1)[0].strip()
    return description if len(description) <= 180 else f"{description[:177].rstrip()}..."


def _compact_view(tool: RegisteredTool) -> RegisteredTool:
    description = tool.compact_description or _fallback_compact_description(tool)
    if description == tool.description:
        return tool
    return replace(tool, description=description)


def _result(payload: dict[str, Any], *, progress: bool) -> ToolExecutionResult:
    """Return discovery data to the model without making it user-facing."""
    return ToolExecutionResult(
        content=json.dumps(payload, default=str),
        details=payload,
        metadata={
            "tool_name": TOOL_SEARCH_NAME,
            MODEL_ONLY_PRESENTATION_KEY: True,
            DISCOVERY_PROGRESS_KEY: progress,
            DISCOVERY_DETAIL_KEYS_KEY: tuple(
                str(match.get("name"))
                for match in payload.get("matches", ())
                if isinstance(match, dict) and match.get("name")
            ),
        },
    )


class ProgressiveToolCatalog:
    """Offer a compact core and expand specialist tools only when requested."""

    def __init__(
        self,
        session: Any,
        source: Callable[[], tuple[RegisteredTool, ...]],
        *,
        enabled: bool,
        base_names: Iterable[str] = (),
        request_names: Iterable[str] = (),
    ) -> None:
        self._session = session
        self._source = source
        self._enabled = enabled
        self._base_names = frozenset(base_names)
        self._request_names = tuple(dict.fromkeys(request_names))
        self._activated: set[str] = set()
        self._expanded: set[str] = set()
        self._last_key: tuple[Any, ...] | None = None
        self._snapshot: tuple[RegisteredTool, ...] = ()
        self._discovery_tool = RegisteredTool(
            name=TOOL_SEARCH_NAME,
            description=(
                "Find hidden tools or expand visible tools to their complete descriptions "
                "and workflow guidance. Search by capability or request exact names. A "
                "successful match updates the schemas available on the next reasoning step."
            ),
            compact_description=(
                "Find hidden tools or expand visible tools with full usage guidance."
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
                        "description": "Exact tool names to reveal or expand when already known.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": TOOL_SEARCH_MAX_RESULTS,
                        "description": "Maximum query matches to return (default 5).",
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
        """Return one immutable session view until visibility or detail changes."""
        full = self._source()
        if not self._enabled:
            return full
        if any(tool.name == TOOL_SEARCH_NAME for tool in full):
            raise ValueError(f"registered tool name {TOOL_SEARCH_NAME!r} is reserved")

        by_name = {tool.name: tool for tool in full}
        initial_names = self._initial_names(by_name)
        visible_names = set(initial_names) | self._activated
        hidden_exists = any(tool.name not in visible_names for tool in full)
        signature = tuple(
            (tool.name, tool.description, tool.compact_description, tool.skill_guidance)
            for tool in full
        )
        key = (
            signature,
            initial_names,
            tuple(sorted(self._activated)),
            tuple(sorted(self._expanded)),
            hidden_exists,
        )
        if key == self._last_key:
            return self._snapshot

        visible: list[RegisteredTool] = []
        if hidden_exists:
            visible.append(_compact_view(self._discovery_tool))
        ordered_names = [*initial_names]
        ordered_names.extend(
            tool.name
            for tool in full
            if tool.name in self._activated and tool.name not in initial_names
        )
        for name in ordered_names:
            tool = by_name.get(name)
            if tool is None:
                continue
            visible.append(tool if name in self._expanded else _compact_view(tool))

        self._last_key = key
        self._snapshot = tuple(visible)
        return self._snapshot

    def _initial_names(self, by_name: dict[str, RegisteredTool]) -> tuple[str, ...]:
        ordered = [
            name
            for name in INITIAL_TOOL_CATALOG_ORDER
            if name != TOOL_SEARCH_NAME and name in by_name
        ]
        dynamic = [name for name in by_name if name not in self._base_names and name not in ordered]
        dynamic[:0] = [
            name for name in self._request_names if name in by_name and name not in ordered
        ]
        if getattr(self._session, "session_goal", None) is not None:
            dynamic.extend(
                name
                for name in SESSION_GOAL_CONTROL_TOOL_NAMES
                if name in by_name and name not in ordered and name not in dynamic
            )
        insert_at = (
            ordered.index(_DYNAMIC_INSERT_AFTER) + 1
            if _DYNAMIC_INSERT_AFTER in ordered
            else min(1, len(ordered))
        )
        prioritized = [*ordered[:insert_at], *dynamic, *ordered[insert_at:]]
        budgeted: list[str] = []
        for name in prioritized[: INITIAL_TOOL_CATALOG_LIMIT - 1]:
            views = [
                _compact_view(self._discovery_tool),
                *(_compact_view(by_name[item]) for item in (*budgeted, name)),
            ]
            schemas = build_openai_tool_specs(views)
            if system_and_tools_overhead(None, schemas) > INITIAL_TOOL_SCHEMA_TOKEN_LIMIT:
                break
            budgeted.append(name)
        return tuple(budgeted)

    def _search(
        self,
        *,
        query: str = "",
        names: Iterable[str] | None = None,
        limit: int = 5,
    ) -> ToolExecutionResult:
        full = self._source()
        by_name = {tool.name: tool for tool in full}
        requested = [str(name).strip() for name in names or () if str(name).strip()]
        not_found = [name for name in requested if name not in by_name]
        matches = [by_name[name] for name in requested if name in by_name]
        clean_query = query.strip()
        if clean_query:
            matches.extend(
                self._query_matches(
                    full,
                    clean_query,
                    limit=max(1, min(limit, TOOL_SEARCH_MAX_RESULTS)),
                )
            )
        unique = {tool.name: tool for tool in matches}
        before_visible = {tool.name for tool in self.snapshot()}
        newly_activated = set(unique) - before_visible
        newly_expanded = set(unique) - self._expanded
        self._activated.update(unique)
        self._expanded.update(unique)
        progress = bool(newly_activated or newly_expanded)
        visible_names = {tool.name for tool in self.snapshot()}
        state = TOOL_DISCOVERY_STATE_MATCHED if unique else TOOL_DISCOVERY_STATE_NO_MATCH
        payload = {
            "ok": bool(unique),
            "state": state,
            "error_kind": None if unique else state,
            "query": clean_query,
            "matches": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "guidance": tool.skill_guidance,
                    "newly_visible": tool.name in newly_activated,
                    "newly_expanded": tool.name in newly_expanded,
                }
                for tool in unique.values()
            ],
            "not_found": not_found,
            "remaining_hidden": sum(tool.name not in visible_names for tool in full),
            "summary": (
                "Matched tools are visible with full descriptions and guidance on the next step."
                if unique
                else "No matching available tool. Continue without discovery or use a known name."
            ),
        }
        record_operation(
            "tool_catalog_expansion",
            {
                "query_present": bool(clean_query),
                "requested_names": requested,
                "matched_names": list(unique),
                "newly_activated": sorted(newly_activated),
                "newly_expanded": sorted(newly_expanded),
                "remaining_hidden": payload["remaining_hidden"],
                "state": state,
            },
        )
        return _result(payload, progress=progress)

    @staticmethod
    def _query_matches(
        tools: tuple[RegisteredTool, ...], query: str, *, limit: int
    ) -> list[RegisteredTool]:
        query_terms = _terms(query)
        ranked: list[tuple[int, str, RegisteredTool]] = []
        for tool in tools:
            name_terms = _terms(tool.name)
            metadata = " ".join(
                (
                    tool.description,
                    tool.compact_description or "",
                    str(tool.source),
                    " ".join(tool.tags),
                    " ".join(tool.use_cases),
                    tool.skill_guidance,
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
