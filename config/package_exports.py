"""PEP 562 hooks that load one sibling module per public name.

Package ``__init__`` files stay facades: they re-export ``__all__``,
``__getattr__``, and ``__dir__`` from a focused ``exports`` module that
calls :func:`bind_package_exports`.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Mapping
from typing import Any


def bind_package_exports(
    package: str,
    exports: Mapping[str, str],
) -> tuple[tuple[str, ...], Callable[[str], Any], Callable[[], list[str]]]:
    """Return ``(__all__, __getattr__, __dir__)`` for a lazy package facade.

    ``exports`` maps each public name to the sibling module that defines it
    (``"billing"``, not ``"config.constants.billing"``). The first access
    imports that sibling and caches the attribute on ``package``. Names that
    are not in ``exports`` are loaded as submodules of ``package`` when they
    exist; otherwise ``AttributeError`` is raised from ``ModuleNotFoundError``.
    """
    public_names = tuple(exports)
    leaf_modules = frozenset(exports.values())

    def __getattr__(name: str) -> Any:
        module = sys.modules[package]
        leaf = exports.get(name)
        if leaf is not None:
            value = getattr(importlib.import_module(f"{package}.{leaf}"), name)
            setattr(module, name, value)
            return value
        try:
            value = importlib.import_module(f"{package}.{name}")
        except ModuleNotFoundError as exc:
            raise AttributeError(f"module {package!r} has no attribute {name!r}") from exc
        setattr(module, name, value)
        return value

    def __dir__() -> list[str]:
        module = sys.modules[package]
        return sorted(set(module.__dict__) | set(public_names) | leaf_modules)

    return public_names, __getattr__, __dir__
