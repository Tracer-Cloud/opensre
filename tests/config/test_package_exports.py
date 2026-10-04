"""Public-name loading for package facades (PEP 562)."""

from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import pytest

import config.constants as constants
from config.constants.exports import EXPORTS
from config.package_exports import bind_package_exports


def test_unknown_name_raises_attribute_error_from_missing_module() -> None:
    package_name = "pkg_exports_missing_probe"
    package = types.ModuleType(package_name)
    sys.modules[package_name] = package
    try:
        _all, getter, _dir = bind_package_exports(package_name, {})
        with pytest.raises(AttributeError, match="has no attribute 'missing'") as caught:
            getter("missing")
        assert isinstance(caught.value.__cause__, ModuleNotFoundError)
    finally:
        del sys.modules[package_name]


def test_resolved_export_is_cached_on_the_package() -> None:
    package_name = "pkg_exports_cache_probe"
    leaf_name = f"{package_name}.leaf"
    package = types.ModuleType(package_name)
    leaf = types.ModuleType(leaf_name)
    leaf.TOKEN = "value"
    sys.modules[package_name] = package
    sys.modules[leaf_name] = leaf
    try:
        _all, getter, _dir = bind_package_exports(package_name, {"TOKEN": "leaf"})
        assert getter("TOKEN") == "value"
        assert package.TOKEN == "value"
        leaf.TOKEN = "patched"
        assert package.TOKEN == "value"
    finally:
        del sys.modules[package_name]
        del sys.modules[leaf_name]


def test_constants_static_reexports_resolve_at_runtime() -> None:
    """Every name ``config.constants`` re-exports for type checkers is importable."""
    tree = ast.parse(Path(constants.__file__).read_text(encoding="utf-8"))
    static = {
        alias.asname or alias.name: node.module.rsplit(".", 1)[-1]
        for block in tree.body
        if isinstance(block, ast.If)
        for node in ast.walk(block)
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
    }
    assert static
    assert {name: EXPORTS.get(name) for name in static} == static
    for name in static:
        getattr(constants, name)
