"""Windows-specific installation discovery and lifecycle cleanup."""

from __future__ import annotations

from surfaces.cli.lifecycle.windows.cleanup import (
    CLEANUP_SCRIPT_PATH,
    read_cleanup_script,
    schedule_windows_cleanup,
    schedule_windows_managed_cleanup,
)
from surfaces.cli.lifecycle.windows.layout import (
    MalformedWindowsInstallError,
    WindowsBinaryInstall,
    classify_windows_binary_install,
    windows_binary_install_paths,
)
from surfaces.cli.lifecycle.windows.powershell import (
    windows_powershell_environment,
    windows_powershell_executable,
)
from surfaces.cli.lifecycle.windows.processes import (
    WindowsProcessIdentity,
    windows_process_identity,
    windows_processes_using_tree,
)

__all__ = [
    "CLEANUP_SCRIPT_PATH",
    "MalformedWindowsInstallError",
    "WindowsBinaryInstall",
    "WindowsProcessIdentity",
    "classify_windows_binary_install",
    "read_cleanup_script",
    "schedule_windows_cleanup",
    "schedule_windows_managed_cleanup",
    "windows_binary_install_paths",
    "windows_powershell_environment",
    "windows_powershell_executable",
    "windows_process_identity",
    "windows_processes_using_tree",
]
