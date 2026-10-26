"""CLI command package — lazy facade; command modules load on demand.

Root ``opensre --help`` imports ``command_specs`` through this package. Keep
``__init__`` free of eager sibling imports so help does not pay for
``registration`` (or any command implementation).
"""

from __future__ import annotations

from config.package_exports import bind_package_exports

__all__, __getattr__, __dir__ = bind_package_exports(
    "surfaces.cli.commands",
    {"register_commands": "registration"},
)
