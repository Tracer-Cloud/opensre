"""Public names for :mod:`surfaces.cli`, resolved on first access."""

from __future__ import annotations

from config.package_exports import bind_package_exports

EXPORTS: dict[str, str] = {
    "write_json": "args",
}

__all__, __getattr__, __dir__ = bind_package_exports("surfaces.cli", EXPORTS)
