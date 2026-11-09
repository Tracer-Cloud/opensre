"""The child process the gateway daemon spawns.

A leaf: it imports only ``sys`` / ``pathlib``, so the CLI command and the
interactive shell's ``/gateway`` command can both read it without either
pulling in the other's package. Naming it inside ``surfaces.gateway_entry``
instead would make the shell import the CLI entrypoint, which re-enters the
shell's own command registry mid-import.

It lives under ``surfaces`` rather than ``gateway`` because the surface owns
its composition root; the gateway supervises whatever child it is handed.

Frozen / ``opensre`` binaries cannot take ``python -m …`` — that sends ``-m``
to Click. Those builds re-enter via ``gateway start --foreground`` so the
child runs the composition root attached (no second daemon spawn).
"""

from __future__ import annotations

import sys
from pathlib import Path

_PYTHON_EXECUTABLE_PREFIXES: tuple[str, ...] = ("python", "pypy")
_FOREGROUND_GATEWAY: tuple[str, ...] = ("gateway", "start", "--foreground")
_MODULE_GATEWAY_ENTRY: tuple[str, ...] = ("-m", "surfaces.gateway_entry")


def _sys_executable_is_python() -> bool:
    return Path(sys.executable).name.lower().startswith(_PYTHON_EXECUTABLE_PREFIXES)


def _current_opensre_entrypoint() -> str | None:
    """Return the current ``opensre`` launcher when this process was started by one."""
    argv0 = sys.argv[0].strip() if sys.argv else ""
    if not argv0:
        return None
    if Path(argv0).name.lower() not in ("opensre", "opensre.exe"):
        return None
    return argv0


def gateway_entry_argv() -> tuple[str, ...]:
    """Argv for the background daemon's child process.

    Call at spawn time (not import time) so frozen / launcher detection can be
    tested and so ``sys.executable`` reflects the current process.
    """
    if entrypoint := _current_opensre_entrypoint():
        return (entrypoint, *_FOREGROUND_GATEWAY)
    if getattr(sys, "frozen", False) or not _sys_executable_is_python():
        return (sys.executable, *_FOREGROUND_GATEWAY)
    return (sys.executable, *_MODULE_GATEWAY_ENTRY)


__all__ = ["gateway_entry_argv"]
