"""Windows PowerShell process helpers for CLI lifecycle operations."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from config.constants.installer import POWERSHELL_MODULE_PATH_ENV

_WINDOWS_POWERSHELL_51_PREAMBLE = (
    "$openSrePowerShellVersion = $PSVersionTable.PSVersion; "
    "if ([string]$PSVersionTable.PSEdition -cne 'Desktop' -or "
    "$openSrePowerShellVersion.Major -ne 5 -or "
    "$openSrePowerShellVersion.Minor -lt 1) { "
    "throw 'OpenSRE lifecycle operations require Windows PowerShell 5.1.' "
    "}\n"
)


def _native_windows_directory() -> Path:
    """Read the trusted Windows directory without consulting caller-controlled env vars."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    get_windows_directory = kernel32.GetWindowsDirectoryW
    get_windows_directory.argtypes = [ctypes.c_wchar_p, wintypes.UINT]
    get_windows_directory.restype = wintypes.UINT

    capacity = 260
    while True:
        buffer = ctypes.create_unicode_buffer(capacity)
        length = int(get_windows_directory(buffer, capacity))
        if length == 0:
            error = int(ctypes.get_last_error())  # type: ignore[attr-defined]
            raise OSError(error, "could not resolve the native Windows directory")
        if length < capacity:
            return Path(buffer.value)
        capacity = length + 1


def windows_powershell_environment() -> dict[str, str]:
    """Return a child environment with trusted Windows roots and host-built module paths."""
    env = os.environ.copy()
    removed_names = {POWERSHELL_MODULE_PATH_ENV.casefold()}
    if os.name == "nt":
        removed_names.update({"systemroot", "windir"})
    for name in tuple(env):
        if name.casefold() in removed_names:
            del env[name]
    if os.name == "nt":
        windows_directory = str(_native_windows_directory())
        env["SystemRoot"] = windows_directory
        env["WINDIR"] = windows_directory
    return env


def windows_powershell_executable() -> str:
    """Return the system Windows PowerShell 5.1 executable, never PowerShell 7."""
    if os.name != "nt":
        # Test-only fallback: production Windows must use the absolute system path.
        return "powershell.exe"
    candidate = (
        _native_windows_directory() / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    if candidate.is_file():
        return str(candidate)
    raise FileNotFoundError(f"Windows PowerShell 5.1 was not found at {candidate}")


def windows_powershell_51_script(script: str) -> str:
    """Prefix a command with a fail-closed Windows PowerShell 5.1 host check."""
    return _WINDOWS_POWERSHELL_51_PREAMBLE + script
