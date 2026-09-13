"""Conservative process discovery and identity checks for Windows lifecycle work."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import surfaces.cli.lifecycle.windows.powershell as powershell
from surfaces.cli.lifecycle.windows.paths import (
    UnsafeWindowsPathError,
    canonical_existing_path,
    same_windows_file,
    windows_path_is_within,
)


@dataclass(frozen=True)
class WindowsProcessIdentity:
    """Stable process identity used to distinguish an exited PID from PID reuse."""

    pid: int
    executable: Path
    started_filetime_utc: int


def _powershell_json(script: str, *, timeout: int) -> tuple[dict[str, Any] | None, str | None]:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    checked_script = powershell.windows_powershell_51_script(script)
    try:
        completed = subprocess.run(
            [
                powershell.windows_powershell_executable(),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                checked_script,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creationflags,
            env=powershell.windows_powershell_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"could not inspect Windows processes: {exc}"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return None, f"could not inspect Windows processes: {detail or 'unknown error'}"

    for line in reversed(completed.stdout.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload, None
    return None, "could not inspect Windows processes: invalid PowerShell response"


def windows_process_identity(
    pid: int,
    *,
    expected_executable: Path | None = None,
) -> tuple[WindowsProcessIdentity | None, str | None]:
    """Read a PID's executable and creation time, failing closed on partial access."""
    if pid <= 0:
        return None, "could not inspect Windows process identity: invalid PID"
    script = rf"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$process = Get-Process -Id {pid} -ErrorAction Stop
$processPath = [string]$process.Path
if (-not $processPath) {{ throw 'process path is unavailable' }}
[ordered]@{{
    pid = [int]$process.Id
    path = [System.IO.Path]::GetFullPath($processPath)
    started_filetime_utc = [int64]$process.StartTime.ToUniversalTime().ToFileTimeUtc()
}} | ConvertTo-Json -Compress
"""
    payload, error = _powershell_json(script, timeout=15)
    if error is not None or payload is None:
        return None, error or "could not inspect Windows process identity"

    raw_pid = payload.get("pid")
    raw_path = payload.get("path")
    raw_started = payload.get("started_filetime_utc")
    if (
        not isinstance(raw_pid, int)
        or raw_pid != pid
        or not isinstance(raw_path, str)
        or not raw_path
        or not isinstance(raw_started, int)
        or raw_started <= 0
    ):
        return None, "could not inspect Windows process identity: invalid response"
    try:
        executable = canonical_existing_path(Path(raw_path))
    except UnsafeWindowsPathError as exc:
        return None, f"could not inspect Windows process identity: {exc}"
    if expected_executable is not None and not same_windows_file(executable, expected_executable):
        return None, "could not verify the Windows process executable identity"
    return WindowsProcessIdentity(raw_pid, executable, raw_started), None


def windows_processes_using_tree(
    app_root: Path,
    *,
    current_pid: int,
) -> tuple[list[tuple[int, str]], str | None]:
    """Return other OpenSRE processes in a tree, or an error on any incomplete scan."""
    try:
        canonical_root = canonical_existing_path(app_root)
    except UnsafeWindowsPathError as exc:
        return [], f"could not inspect running OpenSRE processes: {exc}"

    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$unknown = $false
$matches = @()
try {
    $processes = @(
        Get-Process -ErrorAction Stop |
            Where-Object { $_.ProcessName -ieq 'opensre' }
    )
}
catch {
    $processes = @()
    $unknown = $true
}
foreach ($process in $processes) {
    try {
        $processPath = [string]$process.Path
        $started = [int64]$process.StartTime.ToUniversalTime().ToFileTimeUtc()
    }
    catch {
        $unknown = $true
        continue
    }
    if (-not $processPath -or $started -le 0) {
        $unknown = $true
        continue
    }
    try {
        $fullProcessPath = [System.IO.Path]::GetFullPath($processPath)
    }
    catch {
        $unknown = $true
        continue
    }
    $matches += [ordered]@{
        pid = [int]$process.Id
        path = $fullProcessPath
        started_filetime_utc = $started
    }
}
[ordered]@{
    unknown = $unknown
    processes = @($matches)
} | ConvertTo-Json -Compress -Depth 4
"""
    payload, error = _powershell_json(script, timeout=15)
    if error is not None or payload is None:
        detail = error or "invalid PowerShell response"
        return [], detail.replace("Windows processes", "running OpenSRE processes")
    if payload.get("unknown") is not False:
        return [], "could not verify every running OpenSRE process path"

    raw_processes = payload.get("processes")
    if not isinstance(raw_processes, list):
        return [], "could not inspect running OpenSRE processes: invalid process list"
    processes: list[tuple[int, str]] = []
    for item in raw_processes:
        if not isinstance(item, dict):
            return [], "could not inspect running OpenSRE processes: invalid process entry"
        pid = item.get("pid")
        path = item.get("path")
        started = item.get("started_filetime_utc")
        if (
            not isinstance(pid, int)
            or not isinstance(path, str)
            or not path
            or not isinstance(started, int)
            or started <= 0
        ):
            return [], "could not inspect running OpenSRE processes: invalid process entry"
        if pid == current_pid:
            continue
        process_path = Path(path)
        if not windows_path_is_within(canonical_root, process_path):
            # A process disappearing between enumeration and canonicalization is an
            # incomplete scan, not evidence that the managed tree is idle.
            try:
                canonical_existing_path(process_path)
            except UnsafeWindowsPathError:
                return [], "could not verify every running OpenSRE process path"
            continue
        processes.append((pid, str(canonical_existing_path(process_path))))
    return processes, None
