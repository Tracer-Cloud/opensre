"""Canonical tool registry shared by investigation and chat surfaces."""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from functools import lru_cache
from types import ModuleType

import app.tools as tools_package
from app.tools.base import BaseTool
from app.tools.registered_tool import REGISTERED_TOOL_ATTR, RegisteredTool, ToolSurface

logger = logging.getLogger(__name__)

_SKIP_MODULE_NAMES = {
    "__pycache__",
    "base",
    "registry",
    "registered_tool",
    "tool_decorator",
    "investigation_registry",
    "utils",
    "simple_tools",
    "simple_tools_test",
}

# Preserve the current chat surface while the repo migrates toward explicit
# per-tool surface metadata.
_LEGACY_CHAT_TOOL_NAMES = {
    "fetch_failed_run",
    "get_tracer_run",
    "get_tracer_tasks",
    "get_failed_jobs",
    "get_failed_tools",
    "get_error_logs",
    "get_batch_statistics",
    "get_host_metrics",
    "search_github_code",
    "get_github_file_contents",
    "get_github_repository_tree",
    "list_github_commits",
    "search_sentry_issues",
    "get_sentry_issue_details",
    "list_sentry_issue_events",
}


def _iter_tool_module_names() -> list[str]:
    module_names: list[str] = []
    for module_info in pkgutil.iter_modules(tools_package.__path__):
        if module_info.name in _SKIP_MODULE_NAMES:
            continue
        if module_info.name.startswith("_") or module_info.name.endswith("_test"):
            continue
        module_names.append(module_info.name)
    return sorted(module_names)


def _import_tool_module(module_name: str) -> ModuleType:
    return importlib.import_module(f"{tools_package.__name__}.{module_name}")


def _candidate_belongs_to_module(candidate: object, module_name: str) -> bool:
    if isinstance(candidate, BaseTool):
        return candidate.__class__.__module__ == module_name
    return getattr(candidate, "__module__", None) == module_name


def _default_surfaces_for_tool(tool_name: str) -> tuple[ToolSurface, ...]:
    if tool_name in _LEGACY_CHAT_TOOL_NAMES:
        return ("investigation", "chat")
    return ("investigation",)


def _registered_tool_from_candidate(candidate: object) -> RegisteredTool | None:
    registered = getattr(candidate, REGISTERED_TOOL_ATTR, None)
    if isinstance(registered, RegisteredTool):
        return registered

    if isinstance(candidate, BaseTool):
        explicit_surfaces = getattr(candidate, "surfaces", None) or getattr(
            candidate.__class__,
            "surfaces",
            None,
        )
        return RegisteredTool.from_base_tool(
            candidate,
            surfaces=explicit_surfaces or _default_surfaces_for_tool(candidate.name),
        )

    return None


def _collect_registered_tools_from_module(module: ModuleType) -> list[RegisteredTool]:
    tools_by_name: dict[str, RegisteredTool] = {}
    seen_candidate_ids: set[int] = set()

    for _, candidate in inspect.getmembers(module):
        if not _candidate_belongs_to_module(candidate, module.__name__):
            continue
        candidate_id = id(candidate)
        if candidate_id in seen_candidate_ids:
            continue
        seen_candidate_ids.add(candidate_id)
        registered = _registered_tool_from_candidate(candidate)
        if registered is None:
            continue
        if registered.name in tools_by_name:
            logger.warning(
                "[tools] Duplicate tool name '%s' in module %s; keeping first definition",
                registered.name,
                module.__name__,
            )
            continue
        tools_by_name[registered.name] = registered

    return sorted(tools_by_name.values(), key=lambda tool: tool.name)


@lru_cache(maxsize=1)
def _load_registry_snapshot() -> tuple[RegisteredTool, ...]:
    tools_by_name: dict[str, RegisteredTool] = {}

    for module_name in _iter_tool_module_names():
        try:
            module = _import_tool_module(module_name)
        except ModuleNotFoundError as exc:
            logger.warning("[tools] Skipping %s: %s", module_name, exc)
            continue
        except Exception as exc:  # pragma: no cover - defensive logging path
            logger.warning(
                "[tools] Skipping %s due to import failure: %s",
                module_name,
                exc,
                exc_info=True,
            )
            continue

        for tool in _collect_registered_tools_from_module(module):
            if tool.name in tools_by_name:
                logger.warning(
                    "[tools] Duplicate tool name '%s' across modules; keeping first definition",
                    tool.name,
                )
                continue
            tools_by_name[tool.name] = tool

    return tuple(sorted(tools_by_name.values(), key=lambda tool: tool.name))


def clear_tool_registry_cache() -> None:
    _load_registry_snapshot.cache_clear()


def get_registered_tools(surface: ToolSurface | None = None) -> list[RegisteredTool]:
    tools = list(_load_registry_snapshot())
    if surface is None:
        return tools
    return [tool for tool in tools if surface in tool.surfaces]


def get_registered_tool_map(surface: ToolSurface | None = None) -> dict[str, RegisteredTool]:
    return {tool.name: tool for tool in get_registered_tools(surface)}
