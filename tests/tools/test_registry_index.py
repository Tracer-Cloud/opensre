"""Contract: the static descriptor index matches the imported registry (#3686).

The index (AST scan + pinned fallback descriptors) must equal the imported
registry exactly — same tool names, same surfaces, same source — so surface-
scoped loads built on it can never diverge from the eager snapshot. If this
drifts, a tool changed shape: make its metadata literal, or update the pinned
``_fallback_descriptors`` in ``tools/registry_index.py``.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import tools.registry as registry_module
from core.domain.types.tools import ToolSurface
from tools.registry_index import build_descriptor_index


@pytest.fixture(autouse=True)
def _isolated_registry() -> Iterator[None]:
    # Assert against the on-disk tool set: drop any tools other tests left in the
    # global ``_external_tool_packages`` (the AST index only sees disk).
    saved = list(registry_module._external_tool_packages)
    registry_module._external_tool_packages.clear()
    registry_module.clear_tool_registry_cache()
    yield
    registry_module._external_tool_packages[:] = saved
    registry_module.clear_tool_registry_cache()


def _registered_by_name() -> dict[str, Any]:
    return {tool.name: tool for tool in registry_module.get_registered_tools()}


def test_index_tool_set_matches_registry_exactly() -> None:
    index = set(build_descriptor_index())
    registered = set(_registered_by_name())
    assert index == registered, {
        "missing_from_index": sorted(registered - index),
        "not_registered": sorted(index - registered),
    }


def test_descriptor_surfaces_match_registry() -> None:
    index = build_descriptor_index()
    registered = _registered_by_name()
    mismatched = {
        name: (descriptor.surfaces, tuple(getattr(registered[name], "surfaces", ()) or ()))
        for name, descriptor in index.items()
        if set(descriptor.surfaces) != set(getattr(registered[name], "surfaces", ()) or ())
    }
    assert mismatched == {}


def test_descriptor_source_matches_registry_when_known() -> None:
    index = build_descriptor_index()
    registered = _registered_by_name()
    for name, descriptor in index.items():
        if descriptor.source is None:
            continue
        assert descriptor.source == getattr(registered[name], "source", None), name


def test_descriptor_module_is_dotted_import_path() -> None:
    index = build_descriptor_index()
    assert index["query_datadog_all"].module == "integrations.datadog.tools"
    assert index["shell_run"].module == "tools.interactive_shell.actions.shell"


def test_surface_scoped_load_equals_full_filtered() -> None:
    """The fast surface path must return exactly the full snapshot filtered by surface.

    Compares by ``(name, origin_module)`` so a duplicate tool name that resolves to
    a different module in the surface path (alphabetical) than the full path
    (package-declaration order) fails here rather than silently diverging.
    """
    full = registry_module.get_registered_tools()
    for surface in (ToolSurface.ACTION, ToolSurface.CHAT):
        scoped = {
            (tool.name, tool.origin_module)
            for tool in registry_module.get_registered_tools(surface)
        }
        expected = {
            (tool.name, tool.origin_module)
            for tool in full
            if surface in (getattr(tool, "surfaces", ()) or ())
        }
        assert scoped == expected, surface


def test_get_tool_descriptors_match_surface_load() -> None:
    assert {d.name for d in registry_module.get_tool_descriptors()} == set(build_descriptor_index())
    for surface in (ToolSurface.ACTION, ToolSurface.CHAT):
        descriptor_names = {d.name for d in registry_module.get_tool_descriptors(surface)}
        tool_names = {tool.name for tool in registry_module.get_registered_tools(surface)}
        assert descriptor_names == tool_names, surface


def test_load_tool_materializes_the_executor() -> None:
    # @tool-decorated (AST-indexed) and RegisteredTool-constructed (pinned) both load.
    for name in ("query_datadog_all", "shell_run"):
        descriptor = next(d for d in registry_module.get_tool_descriptors() if d.name == name)
        tool = registry_module.load_tool(descriptor)
        assert tool is not None and tool.name == name


def test_surfaces_attribute_resolution() -> None:
    import ast

    from tools.registry_index import _string_constant

    # ast.Attribute for ToolSurface.CHAT
    node = ast.Attribute(
        value=ast.Name(id="ToolSurface", ctx=ast.Load()),
        attr="CHAT",
        ctx=ast.Load(),
    )
    assert _string_constant(node) == "chat"


def test_baked_index_round_trips_and_serves_frozen_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frozen bundle loads the build-time JSON index instead of scanning source.

    Without it a frozen binary imports every vendor tool module (~1,900 extra
    modules) on the first turn; with it the surface-scoped load is intact.

    This plants the JSON to exercise the load path only. The spec and release
    onedir smoke own the "bundle actually ships the file" contract
    (``test_spec_bakes_the_descriptor_index_into_the_bundle``,
    ``test_release_smoke_asserts_onedir_contains_the_baked_index``).
    """
    from tools import registry_index as ri

    # Arrange: bake the index from source into a fake bundle root.
    reference = ri.build_descriptor_index()
    baked = tmp_path / ri.BAKED_INDEX_RELATIVE_PATH
    ri.dump_descriptor_index(baked)
    assert ri._load_baked_index(baked) == reference

    # Act: pretend to be that frozen bundle.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    ri.clear_descriptor_index_cache()
    registry_module.clear_tool_registry_cache()
    try:
        # Assert: the baked file is what the index serves, and the frozen
        # surface load stays scoped (not the every-vendor fallback).
        assert ri.baked_index_available()
        assert ri.build_descriptor_index() == reference
        action = registry_module._load_surface_snapshot(ToolSurface.ACTION)
        assert 0 < len(action) < len(reference)
        assert all(ToolSurface.ACTION in tool.surfaces for tool in action)
    finally:
        ri.clear_descriptor_index_cache()
        registry_module.clear_tool_registry_cache()
