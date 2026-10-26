"""Canonical tool registry shared by chat and action surfaces.

Facade over tool discovery (:mod:`tools.registry_discovery`), the static
descriptor index (:mod:`tools.registry_index`), and skill-guidance attachment
(:mod:`tools.registry_skill_guidance`): it owns the cached snapshots, the public
lookup API, and the ``ToolRegistry`` port.
"""

from __future__ import annotations

import importlib
import logging
import sys
import threading
from functools import lru_cache
from types import ModuleType
from typing import TYPE_CHECKING

import tools as tools_package
from core.tool import RegisteredTool, ToolSurface
from tools.registry_discovery import (
    INTEGRATION_TOOL_PACKAGES,
    collect_registered_tools_from_module,
    iter_discovered_tool_modules,
)
from tools.registry_skill_guidance import apply_skill_guidance

if TYPE_CHECKING:
    from tools.registry_index import ToolDescriptor

logger = logging.getLogger(__name__)

# Extension point: callers outside ``tools.*`` can register additional
# tool packages by calling :func:`register_external_tool_package`.
# Registered packages are walked the same way as :mod:`tools` — each
# top-level submodule is imported and any ``@tool``-decorated callables
# are picked up.
#
# Production stays clean: with no external registrations, the registry
# discovers only ``tools.*``. The list is *not* persisted across
# processes — every fresh import of opensre starts with zero externals.
_external_tool_packages: list[ModuleType] = []
_external_registration_lock = threading.Lock()


def register_external_tool_package(package: ModuleType) -> None:
    """Register an additional tool package for registry discovery.

    Call before any ``get_registered_tools()`` consumer in the same
    process. The registry cache is cleared so the new package's tools
    appear on the next lookup.

    Idempotent and thread-safe: concurrent callers registering the same
    package (e.g. multiple workers in a ``ThreadPoolExecutor`` each
    importing the same extension on first use) won't add duplicate
    entries that would otherwise produce noisy ``Duplicate tool name``
    warnings on every subsequent registry walk.

    Production code does NOT call this — it's an extension point for
    callers outside ``tools.*`` that ship their own tools but want
    them routed through opensre's agent loop.
    """
    with _external_registration_lock:
        if package in _external_tool_packages:
            return
        _external_tool_packages.append(package)
        clear_tool_registry_cache()


@lru_cache(maxsize=1)
def _load_registry_snapshot() -> tuple[RegisteredTool, ...]:
    tools_by_name: dict[str, RegisteredTool] = {}

    # Walk the canonical tools package, then any per-vendor integration tool
    # packages, then any externally-registered packages in registration order.
    # First definition of a given tool name wins; duplicates are logged and skipped.
    integration_packages: list[ModuleType] = []
    for dotted in INTEGRATION_TOOL_PACKAGES:
        try:
            integration_packages.append(importlib.import_module(dotted))
        except ImportError as exc:
            logger.warning(
                "[tools] Integration tool package %r failed to import: %s",
                dotted,
                exc,
            )
    packages: list[ModuleType] = [
        tools_package,
        *integration_packages,
        *_external_tool_packages,
    ]
    # Integration packages put their tools directly in ``__init__.py`` (one
    # file per vendor), so their own module is a tool source alongside any
    # submodules they may also expose.
    integration_module_ids = {id(pkg) for pkg in integration_packages}
    for package in packages:
        modules_to_scan: list[ModuleType] = []
        if id(package) in integration_module_ids:
            modules_to_scan.append(package)
        modules_to_scan.extend(iter_discovered_tool_modules(package))
        for module in modules_to_scan:
            for tool in collect_registered_tools_from_module(module):
                if tool.name in tools_by_name:
                    logger.warning(
                        "[tools] Duplicate tool name '%s' across modules; keeping first definition",
                        tool.name,
                    )
                    continue
                tools_by_name[tool.name] = tool

    apply_skill_guidance(tools_by_name)
    return tuple(sorted(tools_by_name.values(), key=lambda tool: tool.name))


@lru_cache(maxsize=1)
def _load_registry_tool_map() -> dict[str, RegisteredTool]:
    return {tool.name: tool for tool in _load_registry_snapshot()}


@lru_cache(maxsize=8)
def _load_surface_snapshot(surface: ToolSurface) -> tuple[RegisteredTool, ...]:
    """Import only the modules that statically declare a tool for ``surface``.

    Resolved from the descriptor index so a surface load never imports the other
    surfaces' vendor executors. Runtime-registered external packages are already
    imported, so they are collected directly. Equivalent to the full snapshot
    filtered by ``surface`` (pinned by the registry-index contract test).

    A PyInstaller frozen binary carries bytecode only, so the AST scanner finds
    no source; the build bakes the descriptor index into the bundle instead
    (``dump_descriptor_index``) and this loads it. Only a frozen build that
    shipped without that index falls back to the full dynamic snapshot, which
    imports every vendor tool module.
    """
    from tools.registry_index import baked_index_available, build_descriptor_index

    if getattr(sys, "frozen", False) and not baked_index_available():
        return tuple(t for t in _load_registry_snapshot() if surface in t.surfaces)

    index = build_descriptor_index()
    modules = sorted({d.module for d in index.values() if surface in d.surfaces})
    tools_by_name: dict[str, RegisteredTool] = {}
    for dotted in modules:
        try:
            module = importlib.import_module(dotted)
        except Exception as exc:
            logger.warning("[tools] Skipping %s for surface %r: %s", dotted, surface, exc)
            continue
        for tool in collect_registered_tools_from_module(module):
            if surface in tool.surfaces:
                tools_by_name.setdefault(tool.name, tool)

    for package in _external_tool_packages:
        for module in iter_discovered_tool_modules(package):
            for tool in collect_registered_tools_from_module(module):
                if surface in tool.surfaces:
                    tools_by_name.setdefault(tool.name, tool)

    apply_skill_guidance(tools_by_name, known_tool_names=frozenset(index))
    return tuple(sorted(tools_by_name.values(), key=lambda tool: tool.name))


def clear_tool_registry_cache() -> None:
    _load_registry_snapshot.cache_clear()
    _load_registry_tool_map.cache_clear()
    _load_surface_snapshot.cache_clear()
    _integration_by_tool_name.cache_clear()
    from tools.registry_index import clear_descriptor_index_cache

    clear_descriptor_index_cache()


def get_registered_tools(surface: ToolSurface | None = None) -> list[RegisteredTool]:
    if surface is None:
        return list(_load_registry_snapshot())
    return list(_load_surface_snapshot(surface))


def get_registered_tool(tool_name: str) -> RegisteredTool | None:
    """Look up one tool by name.

    Reads the cached name map directly, so a caller after a single tool does not
    pay for a copy of the whole registry (``get_registered_tool_map``) or a
    linear scan of it (``get_registered_tools``).
    """
    return _load_registry_tool_map().get(tool_name)


@lru_cache(maxsize=1)
def _integration_by_tool_name() -> dict[str, str]:
    """Tool name -> the integration whose package defines it, from the descriptor index."""
    from tools.registry_index import build_descriptor_index

    owners: dict[str, str] = {}
    for name, descriptor in build_descriptor_index().items():
        parts = descriptor.module.split(".")
        if len(parts) > 2 and parts[0] == "integrations":
            owners[name] = parts[1]
    return owners


def integration_of_tool(tool_name: str) -> str | None:
    """The integration that owns ``tool_name`` (``github``, ``s3``, ...), or ``None``.

    Decided by where the registry discovered the tool, not by its evidence
    ``source``, which vendors set independently.
    """
    return _integration_by_tool_name().get(tool_name)


def describe_registered_tool(tool_name: str) -> tuple[str, ...]:
    """A tool's own wording — display name then description — for status copy.

    The turn host renders live tool status but sits below this tier, so it is
    handed this function rather than reaching into the registry itself.
    Returns an empty tuple for an unknown tool.
    """
    tool = get_registered_tool(tool_name)
    if tool is None:
        return ()
    return (tool.display_name or "", tool.description)


def get_registered_tool_map(surface: ToolSurface | None = None) -> dict[str, RegisteredTool]:
    if surface is None:
        return dict(_load_registry_tool_map())
    return {tool.name: tool for tool in get_registered_tools(surface)}


def get_tool_descriptors(surface: ToolSurface | None = None) -> list[ToolDescriptor]:
    """Cheap tool metadata for ``surface`` — reads the static index, imports no
    executor. Use for listing/availability; call :func:`load_tool` to materialize
    an executor only when a tool must run.
    """
    from tools.registry_index import build_descriptor_index

    descriptors = list(build_descriptor_index().values())
    if surface is not None:
        descriptors = [d for d in descriptors if surface in d.surfaces]
    return sorted(descriptors, key=lambda descriptor: descriptor.name)


def load_tool(descriptor: ToolDescriptor) -> RegisteredTool | None:
    """Import a descriptor's module and return its executor — the lazy step.

    Returns ``None`` if the module fails to import or no longer defines the tool.
    """
    try:
        module = importlib.import_module(descriptor.module)
    except Exception as exc:
        logger.warning(
            "[tools] Failed to load %r from %s: %s", descriptor.name, descriptor.module, exc
        )
        return None
    for tool in collect_registered_tools_from_module(module):
        if tool.name == descriptor.name:
            return tool
    return None


class RegisteredToolRegistry:
    """:class:`~core.tool.registry.ToolRegistry` backed by discovered tool packages."""

    def tools_for_surface(self, surface: ToolSurface) -> list[RegisteredTool]:
        return get_registered_tools(surface)

    def tool_map_for_surface(self, surface: ToolSurface) -> dict[str, RegisteredTool]:
        return get_registered_tool_map(surface)


def resolve_tool_display_name(tool_name: str) -> str:
    tool = _load_registry_tool_map().get(tool_name)
    if tool is not None:
        return tool.display_name or tool.name.replace("_", " ")
    return tool_name.replace("_", " ")


def _activity_source_badge(source: str) -> str:
    """Human source name for progress lines.

    Registry sources often end in ``_mcp`` (transport qualifier). Drop that
    last segment so badges read ``Posthog`` / ``Sentry``, not ``Posthog Mcp``.
    """
    parts = [part for part in str(source).replace("-", "_").split("_") if part]
    if len(parts) > 1 and parts[-1].casefold() == "mcp":
        parts = parts[:-1]
    return " ".join(parts).title()


def resolve_tool_activity_labels(tool_name: str) -> tuple[str, str]:
    """Return ``(source_badge, short_label)`` from registry metadata."""
    tool = _load_registry_tool_map().get(tool_name)
    if tool is None:
        return "Tools", tool_name.replace("_", " ")
    source = _activity_source_badge(str(tool.source))
    display = tool.display_name or tool.name.replace("_", " ")
    prefix = f"{source} "
    if display.lower().startswith(prefix.lower()):
        short = display[len(prefix) :].strip()
        return source, short or display
    return source, display
