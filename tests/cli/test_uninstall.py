from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from config.constants.installer import POWERSHELL_MODULE_PATH_ENV
from surfaces.cli.app import cli
from surfaces.cli.lifecycle.uninstall import _remove_path, run_uninstall
from surfaces.cli.lifecycle.windows import (
    WindowsProcessIdentity,
    read_cleanup_script,
    schedule_windows_cleanup,
    schedule_windows_managed_cleanup,
    windows_binary_install_paths,
    windows_process_identity,
    windows_processes_using_tree,
)

_FAILED_PROCESS_ENUMERATOR = r"""
function Get-Process {
    [CmdletBinding()]
    param([int]$Id, [string]$Name)
    if ($PSBoundParameters.ContainsKey('Id')) {
        return $null
    }
    Write-Error 'forced process enumeration failure'
}
"""

_EMPTY_PROCESS_ENUMERATOR = r"""
function Get-Process {
    [CmdletBinding()]
    param([int]$Id, [string]$Name)
    if ($PSBoundParameters.ContainsKey('Id')) {
        return Microsoft.PowerShell.Management\Get-Process @PSBoundParameters
    }
    return @()
}
"""


def _missing_process_identity(
    pid: int, *, expected_executable: Path | None = None
) -> tuple[WindowsProcessIdentity, None]:
    del expected_executable
    return WindowsProcessIdentity(pid, Path(sys.executable).resolve(), 1), None


def _inject_failed_process_enumerator(source: str, *, preference: str) -> str:
    anchor = f"$ErrorActionPreference = {preference}\n"
    assert source.count(anchor) == 1
    return source.replace(anchor, anchor + _FAILED_PROCESS_ENUMERATOR, 1)


def _inject_empty_process_enumerator(source: str) -> str:
    anchor = "$ErrorActionPreference = 'Stop'\n"
    assert source.count(anchor) == 1
    return source.replace(anchor, anchor + _EMPTY_PROCESS_ENUMERATOR, 1)


def _inject_parent_process_state(
    source: str,
    *,
    has_exited: str,
    readable_metadata: bool = False,
) -> str:
    anchor = "$ErrorActionPreference = 'Stop'\n"
    assert source.count(anchor) == 1
    property_body = {
        "true": "return $true",
        "false": "return $false",
        "non-bool": "return 'true'",
        "throw": "throw 'forced HasExited inspection failure'",
        "false-then-true": (
            "$script:openSreTestHasExitedReads += 1\n"
            "        return ($script:openSreTestHasExitedReads -ge 2)"
        ),
    }[has_exited]
    if readable_metadata:
        metadata_setup = r"""
    $parent = [pscustomobject]@{
        ProcessName = 'opensre'
        Path = [string]$payload.parent.path
        StartTime = [System.DateTime]::FromFileTimeUtc(
            [int64]$payload.parent.started_filetime_utc
        )
    }
"""
    else:
        metadata_setup = r"""
    $parent = [pscustomobject]@{ ProcessName = 'opensre' }
    $parent | Add-Member -MemberType ScriptProperty -Name Path -Value {
        throw 'forced parent metadata failure'
    }
"""
    process_override = rf"""
$script:openSreTestHasExitedReads = 0
function Get-Process {{
    [CmdletBinding()]
    param([int]$Id, [string]$Name)
    if (-not $PSBoundParameters.ContainsKey('Id')) {{ return @() }}
{metadata_setup}
    $parent | Add-Member -MemberType ScriptProperty -Name HasExited -Value {{
        {property_body}
    }}
    return $parent
}}
"""
    return source.replace(anchor, anchor + process_override, 1)


def _inject_scan_process_state(
    source: str,
    *,
    target: Path,
    process_name: str,
    process_path: str,
    has_exited: str,
    include_busy_process: bool,
) -> str:
    anchor = "$ErrorActionPreference = 'Stop'\n"
    assert source.count(anchor) == 1
    property_body = {
        "true": "return $true",
        "false": "return $false",
        "throw": "throw 'forced HasExited inspection failure'",
        "false-then-true": (
            "$script:openSreTestScanHasExitedReads += 1\n"
            "        return ($script:openSreTestScanHasExitedReads -ge 2)"
        ),
    }[has_exited]
    process_name_body = {
        "opensre": "return 'opensre'",
        "empty": "return $null",
        "throw": "throw 'forced ProcessName inspection failure'",
    }[process_name]
    target_payload = base64.b64encode(str(target).encode("utf-8")).decode("ascii")
    if process_path == "target":
        process_path_setup = (
            "    $exited | Add-Member -MemberType NoteProperty -Name Path -Value $testTargetPath\n"
        )
    else:
        assert process_path == "throw"
        process_path_setup = r"""
    $exited | Add-Member -MemberType ScriptProperty -Name Path -Value {
        throw 'forced process path inspection failure'
    }
"""
    busy_process = ""
    if include_busy_process:
        busy_process = r"""
    $busy = [pscustomobject]@{
        ProcessName = 'opensre'
        Path = $testTargetPath
        HasExited = $false
    }
    return @($exited, $busy)
"""
    else:
        busy_process = "    return @($exited)\n"
    process_override = rf"""
$script:openSreTestScanHasExitedReads = 0
function Get-Process {{
    [CmdletBinding()]
    param([int]$Id, [string]$Name)
    if ($PSBoundParameters.ContainsKey('Id')) {{
        return Microsoft.PowerShell.Management\Get-Process @PSBoundParameters
    }}
    $testTargetPath = [System.Text.Encoding]::UTF8.GetString(
        [System.Convert]::FromBase64String('{target_payload}')
    )
    $exited = [pscustomobject]@{{}}
    $exited | Add-Member -MemberType ScriptProperty -Name ProcessName -Value {{
        {process_name_body}
    }}
{process_path_setup}
    $exited | Add-Member -MemberType ScriptProperty -Name HasExited -Value {{
        {property_body}
    }}
{busy_process}}}
function Start-Sleep {{
    param([int]$Milliseconds)
    $script:lockDeadline = [System.DateTime]::MinValue
}}
"""
    return source.replace(anchor, anchor + process_override, 1)


def _inject_parent_lookup_failure(source: str) -> str:
    anchor = "$ErrorActionPreference = 'Stop'\n"
    assert source.count(anchor) == 1
    process_override = r"""
function Get-Process {
    [CmdletBinding()]
    param([int]$Id, [string]$Name)
    if ($PSBoundParameters.ContainsKey('Id')) {
        Write-Error -ErrorId 'ForcedParentLookupFailure' 'forced parent lookup failure'
    }
    return @()
}
"""
    return source.replace(anchor, anchor + process_override, 1)


def _inject_late_launch_probe(source: str, *, marker: Path, managed: bool) -> str:
    if managed:
        anchor = "            if ((Test-OpenSreTargetInUse -Path $appRoot -TreatAsDirectory) -or\n"
        executable = 'Join-Path $movedAppRoot "versions\\$expectedInstallId\\opensre.exe"'
    else:
        anchor = "        if ((Test-OpenSreTargetInUse -Path $Path -TreatAsDirectory:$targetWasDirectory) -or\n"
        executable = "Join-Path $retiredPath 'opensre.exe'"
    assert source.count(anchor) == 1
    marker_payload = base64.b64encode(str(marker).encode("utf-8")).decode("ascii")
    probe = f"""
        $lateLaunchMarker = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String('{marker_payload}')
        )
        $lateProcess = $null
        try {{
            $lateExecutable = {executable}
            $lateProcess = Start-Process `
                -FilePath $lateExecutable `
                -ArgumentList @('hold', '30000') `
                -PassThru `
                -WindowStyle Hidden `
                -ErrorAction Stop
            [System.IO.File]::WriteAllText($lateLaunchMarker, 'launched')
        }}
        catch {{
            [System.IO.File]::WriteAllText($lateLaunchMarker, 'blocked')
        }}
        finally {{
            if ($null -ne $lateProcess) {{
                try {{
                    if (-not $lateProcess.HasExited) {{
                        $lateProcess.Kill()
                        $lateProcess.WaitForExit()
                    }}
                }}
                finally {{ $lateProcess.Dispose() }}
            }}
        }}
"""
    return source.replace(anchor, probe + anchor, 1)


def _inject_flat_target_swap_before_retirement(
    source: str,
    *,
    target: Path,
    preserved_target: Path,
    replacement: Path,
) -> str:
    anchor = "        Move-Item -LiteralPath $Path -Destination $retiredPath -ErrorAction Stop\n"
    assert source.count(anchor) == 1
    encoded_paths = [
        base64.b64encode(str(path).encode("utf-8")).decode("ascii")
        for path in (target, preserved_target, replacement)
    ]
    swap = f"""
        $testTarget = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String('{encoded_paths[0]}')
        )
        $testPreservedTarget = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String('{encoded_paths[1]}')
        )
        $testReplacement = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String('{encoded_paths[2]}')
        )
        if ([System.IO.Path]::GetFullPath($Path).Equals(
                [System.IO.Path]::GetFullPath($testTarget),
                [System.StringComparison]::OrdinalIgnoreCase
            )) {{
            [System.IO.File]::Move($testTarget, $testPreservedTarget)
            [System.IO.File]::Move($testReplacement, $testTarget)
        }}
"""
    return source.replace(anchor, swap + anchor, 1)


def _inject_managed_app_swap_before_retirement(
    source: str,
    *,
    app_root: Path,
    preserved_app_root: Path,
    outside_root: Path,
) -> str:
    anchor = (
        "            Move-Item -LiteralPath $appRoot -Destination $movedAppRoot -ErrorAction Stop\n"
    )
    assert source.count(anchor) == 1
    encoded_paths = [
        base64.b64encode(str(path).encode("utf-8")).decode("ascii")
        for path in (app_root, preserved_app_root, outside_root)
    ]
    swap = f"""
            $testAppRoot = [System.Text.Encoding]::UTF8.GetString(
                [System.Convert]::FromBase64String('{encoded_paths[0]}')
            )
            $testPreservedAppRoot = [System.Text.Encoding]::UTF8.GetString(
                [System.Convert]::FromBase64String('{encoded_paths[1]}')
            )
            $testOutsideRoot = [System.Text.Encoding]::UTF8.GetString(
                [System.Convert]::FromBase64String('{encoded_paths[2]}')
            )
            [System.IO.Directory]::Move($testAppRoot, $testPreservedAppRoot)
            New-Item `
                -ItemType Junction `
                -Path $testAppRoot `
                -Target $testOutsideRoot | Out-Null
"""
    return source.replace(anchor, swap + anchor, 1)


def _inject_directory_target_swap_before_retirement(
    source: str,
    *,
    target: Path,
    preserved_target: Path,
    replacement: Path,
) -> str:
    anchor = "        Move-Item -LiteralPath $Path -Destination $retiredPath -ErrorAction Stop\n"
    assert source.count(anchor) == 1
    encoded_paths = [
        base64.b64encode(str(path).encode("utf-8")).decode("ascii")
        for path in (target, preserved_target, replacement)
    ]
    swap = f"""
        $testTarget = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String('{encoded_paths[0]}')
        )
        $testPreservedTarget = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String('{encoded_paths[1]}')
        )
        $testReplacement = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String('{encoded_paths[2]}')
        )
        if ([System.IO.Path]::GetFullPath($Path).Equals(
                [System.IO.Path]::GetFullPath($testTarget),
                [System.StringComparison]::OrdinalIgnoreCase
            )) {{
            [System.IO.Directory]::Move($testTarget, $testPreservedTarget)
            [System.IO.Directory]::Move($testReplacement, $testTarget)
        }}
"""
    return source.replace(anchor, swap + anchor, 1)


def _inject_retired_target_swap_before_removal(
    source: str,
    *,
    preserved_target: Path,
    replacement: Path,
    directory: bool,
) -> str:
    anchor = "            $deletionLease = Open-OpenSreDeletionLease `\n"
    assert source.count(anchor) == 1
    encoded_paths = [
        base64.b64encode(str(path).encode("utf-8")).decode("ascii")
        for path in (preserved_target, replacement)
    ]
    move_type = "Directory" if directory else "File"
    swap = f"""
            if (-not $script:testRetiredTargetSwapped) {{
                $script:testRetiredTargetSwapped = $true
                $testPreservedTarget = [System.Text.Encoding]::UTF8.GetString(
                    [System.Convert]::FromBase64String('{encoded_paths[0]}')
                )
                $testReplacement = [System.Text.Encoding]::UTF8.GetString(
                    [System.Convert]::FromBase64String('{encoded_paths[1]}')
                )
                [System.IO.{move_type}]::Move($target, $testPreservedTarget)
                [System.IO.{move_type}]::Move($testReplacement, $target)
            }}
"""
    return source.replace(anchor, swap + anchor, 1)


def _inject_child_junction_before_recursive_deletion(
    source: str,
    *,
    child_name: str,
    preserved_child: Path,
    outside_root: Path,
) -> str:
    anchor = "            $deletionLease.PrepareTree()\n"
    assert source.count(anchor) == 1
    encoded_values = [
        base64.b64encode(value.encode("utf-8")).decode("ascii")
        for value in (child_name, str(preserved_child), str(outside_root))
    ]
    swap = f"""
            if (-not $script:testChildJunctionSwapped -and
                [string]$RetiredTarget.ExpectedIdentity.kind -ceq 'directory') {{
                $script:testChildJunctionSwapped = $true
                $testChildName = [System.Text.Encoding]::UTF8.GetString(
                    [System.Convert]::FromBase64String('{encoded_values[0]}')
                )
                $testChild = Join-Path $target $testChildName
                $testPreservedChild = [System.Text.Encoding]::UTF8.GetString(
                    [System.Convert]::FromBase64String('{encoded_values[1]}')
                )
                $testOutsideRoot = [System.Text.Encoding]::UTF8.GetString(
                    [System.Convert]::FromBase64String('{encoded_values[2]}')
                )
                [System.IO.Directory]::Move($testChild, $testPreservedChild)
                New-Item `
                    -ItemType Junction `
                    -Path $testChild `
                    -Target $testOutsideRoot | Out-Null
            }}
"""
    return source.replace(anchor, swap + anchor, 1)


def _inject_launch_probe_before_recursive_deletion(
    source: str,
    *,
    marker: Path,
    executable_relative_path: Path,
) -> str:
    anchor = "        $preparedTarget.Lease.DeleteFiles()\n"
    assert source.count(anchor) == 1
    encoded_values = [
        base64.b64encode(str(value).encode("utf-8")).decode("ascii")
        for value in (marker, executable_relative_path)
    ]
    probe = f"""
        if (-not $script:testDeletionLaunchProbed -and
            [string]$preparedTarget.RetiredTarget.ExpectedIdentity.kind -ceq 'directory') {{
                $script:testDeletionLaunchProbed = $true
                $testLaunchMarker = [System.Text.Encoding]::UTF8.GetString(
                    [System.Convert]::FromBase64String('{encoded_values[0]}')
                )
                $testRelativeExecutable = [System.Text.Encoding]::UTF8.GetString(
                    [System.Convert]::FromBase64String('{encoded_values[1]}')
                )
                $testExecutable = Join-Path `
                    ([string]$preparedTarget.RetiredTarget.Path) `
                    $testRelativeExecutable
                $testLateProcess = $null
                try {{
                    $testLateProcess = Start-Process `
                        -FilePath $testExecutable `
                        -ArgumentList @('hold', '30000') `
                        -PassThru `
                        -WindowStyle Hidden `
                        -ErrorAction Stop
                    [System.IO.File]::WriteAllText($testLaunchMarker, 'launched')
                }}
                catch {{
                    [System.IO.File]::WriteAllText($testLaunchMarker, 'blocked')
                }}
                finally {{
                    if ($null -ne $testLateProcess) {{
                        try {{
                            if (-not $testLateProcess.HasExited) {{
                                $testLateProcess.Kill()
                                $testLateProcess.WaitForExit()
                            }}
                        }}
                        finally {{ $testLateProcess.Dispose() }}
                    }}
                }}
        }}
"""
    return source.replace(anchor, anchor + probe, 1)


def _inject_missing_data_target_before_decision(source: str, *, target: Path) -> str:
    anchor = "$dataFailed = $false\n"
    assert source.count(anchor) == 1
    target_payload = base64.b64encode(str(target).encode("utf-8")).decode("ascii")
    create_target = f"""
$testMissingDataTarget = [System.Text.Encoding]::UTF8.GetString(
    [System.Convert]::FromBase64String('{target_payload}')
)
[System.IO.Directory]::CreateDirectory($testMissingDataTarget) | Out-Null
[System.IO.File]::WriteAllText(
    (Join-Path $testMissingDataTarget 'unrelated.txt'),
    'preserve'
)
"""
    return source.replace(anchor, create_target + anchor, 1)


def _capture_cleanup_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> list[subprocess.Popen[bytes]]:
    real_popen = subprocess.Popen
    workers: list[subprocess.Popen[bytes]] = []

    def _capture_worker(args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        if "-CleanupScriptPath" not in args:
            return real_popen(args, **kwargs)
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        worker = real_popen(args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.subprocess.Popen", _capture_worker)
    return workers


def _inject_deletion_preparation_failure(source: str, *, fail_on: int = 2) -> str:
    anchor = "$preparedRetiredTargets = @()\n"
    assert source.count(anchor) == 1
    failure = rf"""
$script:testDeletionLeaseOpenCount = 0
function Open-OpenSreDeletionLease {{
    param(
        [string]$Path,
        [psobject]$ExpectedIdentity
    )
    $script:testDeletionLeaseOpenCount++
    if ($script:testDeletionLeaseOpenCount -eq {fail_on}) {{
        throw [System.InvalidOperationException]::new(
            "forced retired-target preparation failure: $Path"
        )
    }}
    return [OpenSre.CleanupNativePathApi]::OpenDeletionLease(
        (ConvertTo-OpenSreExtendedPath -Path $Path),
        (ConvertTo-OpenSreComparablePath -Path $Path),
        [uint32]$ExpectedIdentity.volume_serial_number,
        [uint64]$ExpectedIdentity.file_index,
        [int64]$ExpectedIdentity.creation_filetime_utc,
        ([string]$ExpectedIdentity.kind -ceq 'directory')
    )
}}

"""
    return source.replace(anchor, failure + anchor, 1)


def _inject_install_rollback_barrier(
    source: str,
    *,
    app_root: Path,
    launcher: Path,
    ready: Path,
    release: Path,
) -> str:
    anchor = "                -RetiredPath ([string]$retiredTarget.Path)\n"
    assert source.count(anchor) == 1
    encoded_paths = [
        base64.b64encode(str(path).encode("utf-8")).decode("ascii")
        for path in (app_root, launcher, ready, release)
    ]
    barrier = f"""
            if (-not $script:testInstallRollbackPaused) {{
                $testExpectedPath = [System.IO.Path]::GetFullPath(
                    [string]$retiredTarget.ExpectedIdentity.path
                )
                $testAppRoot = [System.Text.Encoding]::UTF8.GetString(
                    [System.Convert]::FromBase64String('{encoded_paths[0]}')
                )
                $testLauncher = [System.Text.Encoding]::UTF8.GetString(
                    [System.Convert]::FromBase64String('{encoded_paths[1]}')
                )
                if ($testExpectedPath.Equals(
                        [System.IO.Path]::GetFullPath($testAppRoot),
                        [System.StringComparison]::OrdinalIgnoreCase
                    ) -or $testExpectedPath.Equals(
                        [System.IO.Path]::GetFullPath($testLauncher),
                        [System.StringComparison]::OrdinalIgnoreCase
                    )) {{
                    $script:testInstallRollbackPaused = $true
                    $testReady = [System.Text.Encoding]::UTF8.GetString(
                        [System.Convert]::FromBase64String('{encoded_paths[2]}')
                    )
                    $testRelease = [System.Text.Encoding]::UTF8.GetString(
                        [System.Convert]::FromBase64String('{encoded_paths[3]}')
                    )
                    [System.IO.File]::WriteAllText($testReady, 'ready')
                    while (-not [System.IO.File]::Exists($testRelease)) {{
                        Start-Sleep -Milliseconds 50
                    }}
                }}
            }}
"""
    return source.replace(anchor, anchor + barrier, 1)


def _inject_data_decision_barrier(source: str, *, ready: Path, release: Path) -> str:
    anchor = "$dataFailed = $false\n"
    assert source.count(anchor) == 1
    ready_payload = base64.b64encode(str(ready).encode("utf-8")).decode("ascii")
    release_payload = base64.b64encode(str(release).encode("utf-8")).decode("ascii")
    barrier = f"""
$dataDecisionReady = [System.Text.Encoding]::UTF8.GetString(
    [System.Convert]::FromBase64String('{ready_payload}')
)
$dataDecisionRelease = [System.Text.Encoding]::UTF8.GetString(
    [System.Convert]::FromBase64String('{release_payload}')
)
[System.IO.File]::WriteAllText($dataDecisionReady, 'ready')
while (-not (Test-Path -LiteralPath $dataDecisionRelease -PathType Leaf)) {{
    Start-Sleep -Milliseconds 50
}}

"""
    return source.replace(anchor, barrier + anchor, 1)


def _inject_before_data_guard_barrier(source: str, *, ready: Path, release: Path) -> str:
    anchor = "if ($deleteData) {\n    try {\n        foreach ($guardPathValue in @($payload.data_guard_paths)) {\n"
    assert source.count(anchor) == 1
    ready_payload = base64.b64encode(str(ready).encode("utf-8")).decode("ascii")
    release_payload = base64.b64encode(str(release).encode("utf-8")).decode("ascii")
    barrier = f"""
$dataGuardReady = [System.Text.Encoding]::UTF8.GetString(
    [System.Convert]::FromBase64String('{ready_payload}')
)
$dataGuardRelease = [System.Text.Encoding]::UTF8.GetString(
    [System.Convert]::FromBase64String('{release_payload}')
)
[System.IO.File]::WriteAllText($dataGuardReady, 'ready')
while (-not (Test-Path -LiteralPath $dataGuardRelease -PathType Leaf)) {{
    Start-Sleep -Milliseconds 50
}}

"""
    return source.replace(anchor, barrier + anchor, 1)


def _inject_final_lock_parent_swap(
    source: str,
    *,
    install_parent: Path,
    preserved_parent: Path,
    outside_parent: Path,
) -> str:
    anchor = "        Assert-OpenSreSafeAncestorChain -Path $deleteLockPath\n"
    assert source.count(anchor) == 1
    encoded_paths = [
        base64.b64encode(str(path).encode("utf-8")).decode("ascii")
        for path in (install_parent, preserved_parent, outside_parent)
    ]
    swap = f"""
        # Simulate losing the verified handle at the last possible boundary,
        # then replace its ancestor before any path-based fallback can run.
        if ($null -ne $lockHandle) {{
            $lockHandle.Dispose()
            $lockHandle = $null
        }}
        $testInstallParent = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String('{encoded_paths[0]}')
        )
        $testPreservedParent = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String('{encoded_paths[1]}')
        )
        $testOutsideParent = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String('{encoded_paths[2]}')
        )
        [System.IO.Directory]::Move($testInstallParent, $testPreservedParent)
        New-Item `
            -ItemType Junction `
            -Path $testInstallParent `
            -Target $testOutsideParent | Out-Null
"""
    return source.replace(anchor, anchor + swap, 1)


def _short_path_or_skip(path: Path) -> Path:
    buffer = ctypes.create_unicode_buffer(32768)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_short_path = kernel32.GetShortPathNameW
    get_short_path.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    get_short_path.restype = ctypes.c_uint32
    length = get_short_path(str(path), buffer, len(buffer))
    if length == 0:
        raise ctypes.WinError(ctypes.get_last_error())
    assert length < len(buffer)
    short_path = Path(buffer.value)
    if not short_path or str(short_path).casefold() == str(path).casefold():
        pytest.skip("8.3 aliases are disabled on the test volume")
    return short_path


def _start_hidden_windows_process(executable: Path, release: Path) -> int:
    powershell = (
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    literal = "'" + str(executable).replace("'", "''") + "'"
    release_literal = "'\"" + str(release).replace("'", "''") + "\"'"
    completed = subprocess.run(
        [
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "$ErrorActionPreference = 'Stop'; "
                f"$process = Start-Process -FilePath {literal} "
                f"-ArgumentList @('hold-until', {release_literal}) -PassThru -WindowStyle Hidden; "
                "if ($null -eq $process) { throw 'Start-Process returned no process' }; "
                "try { [Console]::Out.WriteLine([int]$process.Id) } finally { $process.Dispose() }"
            ),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return int(completed.stdout.strip().splitlines()[-1])


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink creation requires Developer Mode or elevation")
        raise


def test_remove_path_removes_file(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("data")
    ok, err = _remove_path(f)
    assert ok is True
    assert err is None
    assert not f.exists()


def test_remove_path_removes_directory(tmp_path: Path) -> None:
    d = tmp_path / "subdir"
    d.mkdir()
    (d / "child.txt").write_text("x")
    ok, err = _remove_path(d)
    assert ok is True
    assert err is None
    assert not d.exists()


def test_remove_path_nonexistent_returns_ok(tmp_path: Path) -> None:
    ok, err = _remove_path(tmp_path / "does_not_exist")
    assert ok is True
    assert err is None


def test_remove_path_removes_broken_symlink(tmp_path: Path) -> None:
    link = tmp_path / "broken"
    _symlink_or_skip(link, tmp_path / "missing")

    ok, err = _remove_path(link)

    assert ok is True
    assert err is None
    assert not link.exists()
    assert not link.is_symlink()


def test_remove_path_returns_error_on_permission_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = tmp_path / "locked"
    d.mkdir()

    def _raise(path: str) -> None:
        raise OSError("Permission denied")

    monkeypatch.setattr("shutil.rmtree", _raise)
    ok, err = _remove_path(d)
    assert ok is False
    assert "Permission denied" in (err or "")


def test_run_uninstall_cancelled_by_user(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: False)

    import questionary as _q

    def _confirm_no(*_args: object, **_kwargs: object) -> object:
        return type("Q", (), {"ask": lambda _self: False})()

    monkeypatch.setattr(_q, "confirm", _confirm_no)

    rc = run_uninstall(yes=False)

    assert rc == 0
    assert "Cancelled" in capsys.readouterr().out


def test_run_uninstall_aborted_by_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: False)

    import questionary as _q

    def _raise_interrupt(*a: object, **kw: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(_q, "confirm", _raise_interrupt)

    rc = run_uninstall(yes=False)

    assert rc == 1
    assert "Aborted" in capsys.readouterr().out


def test_run_uninstall_skips_missing_dirs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    missing = tmp_path / "does_not_exist"
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [missing])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: False)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._pip_uninstall", lambda: 0)

    rc = run_uninstall(yes=True)

    assert rc == 0
    out = capsys.readouterr().out
    assert "not found" in out
    assert "skipped" in out


def test_run_uninstall_removes_existing_dir(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    d = tmp_path / "tracer_home"
    d.mkdir()
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [d])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: False)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._pip_uninstall", lambda: 0)

    rc = run_uninstall(yes=True)

    assert rc == 0
    assert not d.exists()
    assert "deleted" in capsys.readouterr().out


def test_run_uninstall_pip_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: False)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._pip_uninstall", lambda: 0)

    rc = run_uninstall(yes=True)

    assert rc == 0
    assert "opensre has been uninstalled" in capsys.readouterr().out


def test_run_uninstall_pip_failure_shows_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: False)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._pip_uninstall", lambda: 1)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: False)

    rc = run_uninstall(yes=True)

    assert rc == 1
    err = capsys.readouterr().err
    assert "pip uninstall failed" in err
    assert "retry manually" in err


def test_run_uninstall_pip_failure_windows_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: False)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._pip_uninstall", lambda: 1)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: True)

    rc = run_uninstall(yes=True)

    assert rc == 1
    assert "pip uninstall" in capsys.readouterr().err


def test_run_uninstall_binary_removes_executable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fake_exe = tmp_path / "opensre"
    fake_exe.write_bytes(b"\x7fELF")
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: False)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.sys.executable", str(fake_exe))

    rc = run_uninstall(yes=True)

    assert rc == 0
    assert not fake_exe.exists()
    assert "binary" in capsys.readouterr().out


def test_run_uninstall_onedir_binary_removes_launcher_and_app_dir(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    install_dir = tmp_path / "bin"
    app_dir = install_dir / ".opensre-app"
    internal = app_dir / "_internal"
    internal.mkdir(parents=True)
    fake_exe = app_dir / "opensre"
    fake_exe.write_bytes(b"\x7fELF")
    launcher = install_dir / "opensre"
    _symlink_or_skip(launcher, fake_exe)

    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: False)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.sys.executable", str(fake_exe))
    monkeypatch.setattr("shutil.which", lambda _name: str(launcher))

    rc = run_uninstall(yes=True)

    assert rc == 0
    assert not launcher.exists()
    assert not launcher.is_symlink()
    assert not app_dir.exists()
    out = capsys.readouterr().out
    assert str(launcher) in out
    assert str(app_dir) in out


def test_windows_install_paths_find_only_owned_layout_files(tmp_path: Path) -> None:
    install_dir = tmp_path / "install dir"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "build-1"
    (version_dir / "_internal").mkdir(parents=True)
    executable = version_dir / "opensre.exe"
    executable.write_bytes(b"MZ")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    install_lock = install_dir / ".opensre-app.install.lock"
    install_lock.write_bytes(b"")
    legacy_executable = install_dir / "opensre.exe"
    legacy_executable.write_bytes(b"MZ")
    unrelated = install_dir / "keep-me.txt"
    unrelated.write_text("keep", encoding="utf-8")

    paths = windows_binary_install_paths(executable)

    assert paths == [launcher, app_root, install_lock]
    assert legacy_executable not in paths
    assert unrelated not in paths
    assert install_dir not in paths


def test_windows_install_paths_preserve_unowned_launcher(tmp_path: Path) -> None:
    install_dir = tmp_path / "bin"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "build-1"
    version_dir.mkdir(parents=True)
    executable = version_dir / "opensre.exe"
    executable.write_bytes(b"MZ")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\r\necho user-owned\r\n", encoding="utf-8")

    paths = windows_binary_install_paths(executable)

    assert paths == [app_root]
    assert launcher not in paths


@pytest.mark.parametrize("marker_text", (None, "not an OpenSRE ownership marker\n"))
def test_windows_uninstall_refuses_malformed_managed_layout_before_deleting_data(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    marker_text: str | None,
) -> None:
    install_dir = tmp_path / "malformed managed install"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "build-1"
    (version_dir / "_internal").mkdir(parents=True)
    executable = version_dir / "opensre.exe"
    executable.write_bytes(b"MZ")
    marker = app_root / "layout-v1.marker"
    if marker_text is not None:
        marker.write_text(marker_text, encoding="utf-8")
    (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    data_dir = tmp_path / "user data"
    data_dir.mkdir()
    (data_dir / "state.json").write_text("keep", encoding="utf-8")

    def _unexpected_schedule(*_args: object, **_kwargs: object) -> tuple[bool, str | None]:
        raise AssertionError("malformed layout must not schedule cleanup")

    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [data_dir])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.sys.executable", str(executable))
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.schedule_windows_cleanup", _unexpected_schedule
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.schedule_windows_managed_cleanup",
        _unexpected_schedule,
    )

    rc = run_uninstall(yes=True)

    captured = capsys.readouterr()
    assert rc == 1
    assert "marker is missing or unreadable" in captured.err or "marker is invalid" in captured.err
    assert "Nothing was deleted" in captured.err
    assert "install.ps1" in captured.err
    assert executable.is_file()
    assert app_root.is_dir()
    assert launcher.is_file()
    assert (data_dir / "state.json").read_text(encoding="utf-8") == "keep"


def test_windows_uninstall_refuses_unmanaged_onedir_before_deleting_data(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "extracted release" / "opensre"
    payload = bundle_root / "_internal" / "payload.dat"
    payload.parent.mkdir(parents=True)
    payload.write_text("keep the complete bundle", encoding="utf-8")
    executable = bundle_root / "opensre.exe"
    executable.write_bytes(b"MZ")
    data_dir = tmp_path / "user data"
    data_dir.mkdir()
    data_file = data_dir / "state.json"
    data_file.write_text("keep", encoding="utf-8")

    def _unexpected_schedule(*_args: object, **_kwargs: object) -> tuple[bool, str | None]:
        raise AssertionError("unmanaged onedir must not schedule partial cleanup")

    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [data_dir])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.sys.executable", str(executable))
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.schedule_windows_cleanup", _unexpected_schedule
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.schedule_windows_managed_cleanup",
        _unexpected_schedule,
    )

    rc = run_uninstall(yes=True)

    captured = capsys.readouterr()
    assert rc == 1
    assert "unpacked Windows onedir bundle" in captured.err
    assert "install.ps1" in captured.err
    assert "Nothing was deleted" in captured.err
    assert executable.is_file()
    assert payload.read_text(encoding="utf-8") == "keep the complete bundle"
    assert data_file.read_text(encoding="utf-8") == "keep"


def test_windows_uninstall_refuses_renamed_flat_binary_before_deleting_data(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "renamed frozen executable"
    install_dir.mkdir()
    executable = install_dir / "other-product.exe"
    executable.write_bytes(b"MZ")
    data_dir = tmp_path / "renamed binary user data"
    data_dir.mkdir()
    data_file = data_dir / "state.json"
    data_file.write_text("keep", encoding="utf-8")

    def _unexpected_schedule(*_args: object, **_kwargs: object) -> tuple[bool, str | None]:
        raise AssertionError("a renamed executable must not schedule cleanup")

    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [data_dir])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.sys.executable", str(executable))
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.schedule_windows_cleanup", _unexpected_schedule
    )

    rc = run_uninstall(yes=True)

    captured = capsys.readouterr()
    assert rc == 1
    assert "historical Windows binary name is invalid" in captured.err
    assert "Nothing was deleted" in captured.err
    assert executable.read_bytes() == b"MZ"
    assert data_file.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction regression")
def test_windows_uninstall_rejects_junction_anywhere_in_raw_executable_ancestors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real parent"
    install_dir = real_parent / "nested" / "bin"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "build-1"
    version_dir.mkdir(parents=True)
    executable = version_dir / "opensre.exe"
    executable.write_bytes(b"MZ")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    data_dir = tmp_path / "junction user data"
    data_dir.mkdir()
    data_file = data_dir / "state.json"
    data_file.write_text("keep", encoding="utf-8")

    alias_parent = tmp_path / "aliased parent"
    linked = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/c", "mklink", "/J", str(alias_parent), str(real_parent)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert linked.returncode == 0, linked.stdout + linked.stderr
    aliased_executable = alias_parent / "nested" / "bin" / executable.relative_to(install_dir)

    def _unexpected_schedule(*_args: object, **_kwargs: object) -> tuple[bool, str | None]:
        raise AssertionError("a junctioned executable path must not schedule cleanup")

    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [data_dir])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.sys.executable", str(aliased_executable))
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.schedule_windows_managed_cleanup",
        _unexpected_schedule,
    )

    try:
        rc = run_uninstall(yes=True)

        captured = capsys.readouterr()
        assert rc == 1
        assert "reparse point" in captured.err
        assert "Nothing was deleted" in captured.err
        assert executable.read_bytes() == b"MZ"
        assert launcher.is_file()
        assert data_file.read_text(encoding="utf-8") == "keep"
    finally:
        if alias_parent.is_junction():
            alias_parent.rmdir()


def test_windows_uninstall_refuses_malformed_managed_executable_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "bin" / ".opensre-app"
    malformed_version = app_root / "build-without-versions-parent"
    malformed_version.mkdir(parents=True)
    executable = malformed_version / "opensre.exe"
    executable.write_bytes(b"MZ")
    unrelated = tmp_path / "user-data.json"
    unrelated.write_text("keep", encoding="utf-8")

    def _unexpected_schedule(*_args: object, **_kwargs: object) -> tuple[bool, str | None]:
        raise AssertionError("malformed managed path must not schedule partial cleanup")

    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [unrelated])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.sys.executable", str(executable))
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.schedule_windows_cleanup", _unexpected_schedule
    )

    rc = run_uninstall(yes=True)

    captured = capsys.readouterr()
    assert rc == 1
    assert "managed Windows executable path is malformed" in captured.err
    assert "Nothing was deleted" in captured.err
    assert executable.is_file()
    assert unrelated.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize(
    ("pointer_text", "expected_error"),
    (
        (None, "pointer is missing or unreadable"),
        ("../outside\n", "pointer is invalid"),
        ("..\n", "pointer is invalid"),
        (".\n", "pointer is invalid"),
        (".build\n", "pointer is invalid"),
        ("build.\n", "pointer is invalid"),
        ("-build\n", "pointer is invalid"),
        ("build-\n", "pointer is invalid"),
        (" build-1\n", "pointer is invalid"),
        ("missing-build\n", "pointer is dangling"),
    ),
)
def test_windows_uninstall_refuses_malformed_current_pointer_before_deleting_data(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    pointer_text: str | None,
    expected_error: str,
) -> None:
    install_dir = tmp_path / "malformed pointer install"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "build-1"
    (version_dir / "_internal").mkdir(parents=True)
    executable = version_dir / "opensre.exe"
    executable.write_bytes(b"MZ")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    if pointer_text is not None:
        (app_root / "current.txt").write_text(pointer_text, encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    data_dir = tmp_path / "pointer user data"
    data_dir.mkdir()
    (data_dir / "state.json").write_text("keep", encoding="utf-8")

    def _unexpected_schedule(*_args: object, **_kwargs: object) -> tuple[bool, str | None]:
        raise AssertionError("malformed layout must not schedule cleanup")

    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [data_dir])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.sys.executable", str(executable))
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.schedule_windows_managed_cleanup",
        _unexpected_schedule,
    )

    rc = run_uninstall(yes=True)

    captured = capsys.readouterr()
    assert rc == 1
    assert expected_error in captured.err
    assert "Nothing was deleted" in captured.err
    assert executable.is_file()
    assert launcher.is_file()
    assert (data_dir / "state.json").read_text(encoding="utf-8") == "keep"


def test_windows_uninstall_rejects_dot_pointer_even_when_it_names_same_file(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "dot pointer install"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "build-1"
    (version_dir / "_internal").mkdir(parents=True)
    executable = version_dir / "opensre.exe"
    executable.write_bytes(b"MZ")
    os.link(executable, app_root / "opensre.exe")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("..\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    data_dir = tmp_path / "dot pointer data"
    data_dir.mkdir()
    data_file = data_dir / "state.json"
    data_file.write_text("keep", encoding="utf-8")

    def _unexpected_schedule(*_args: object, **_kwargs: object) -> tuple[bool, str | None]:
        raise AssertionError("a traversal pointer must not schedule cleanup")

    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [data_dir])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.sys.executable", str(executable))
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.schedule_windows_managed_cleanup",
        _unexpected_schedule,
    )

    rc = run_uninstall(yes=True)

    captured = capsys.readouterr()
    assert rc == 1
    assert "current-version pointer is invalid" in captured.err
    assert "Nothing was deleted" in captured.err
    assert executable.is_file()
    assert launcher.is_file()
    assert data_file.read_text(encoding="utf-8") == "keep"


def test_windows_uninstall_refuses_stale_managed_version_before_deleting_data(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "stale managed process"
    app_root = install_dir / ".opensre-app"
    old_version = app_root / "versions" / "old-build"
    current_version = app_root / "versions" / "current-build"
    (old_version / "_internal").mkdir(parents=True)
    (current_version / "_internal").mkdir(parents=True)
    executable = old_version / "opensre.exe"
    executable.write_bytes(b"MZ-old")
    current_executable = current_version / "opensre.exe"
    current_executable.write_bytes(b"MZ-current")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("current-build\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    data_dir = tmp_path / "stale process data"
    data_dir.mkdir()
    data_file = data_dir / "state.json"
    data_file.write_text("keep", encoding="utf-8")

    def _unexpected_schedule(*_args: object, **_kwargs: object) -> tuple[bool, str | None]:
        raise AssertionError("stale managed process must not schedule cleanup")

    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [data_dir])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.sys.executable", str(executable))
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.schedule_windows_cleanup", _unexpected_schedule
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.schedule_windows_managed_cleanup",
        _unexpected_schedule,
    )

    rc = run_uninstall(yes=True)

    captured = capsys.readouterr()
    assert rc == 1
    assert "not the version selected" in captured.err
    assert "new PowerShell window" in captured.err
    assert "Nothing was deleted" in captured.err
    assert executable.is_file()
    assert current_executable.is_file()
    assert launcher.is_file()
    assert data_file.read_text(encoding="utf-8") == "keep"


def test_windows_uninstall_refuses_second_process_before_deleting_data(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "busy managed install"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "build-1"
    (version_dir / "_internal").mkdir(parents=True)
    executable = version_dir / "opensre.exe"
    executable.write_bytes(b"MZ")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    data_dir = tmp_path / "busy user data"
    data_dir.mkdir()
    scheduled = False

    def _running_processes(
        root: Path, *, current_pid: int
    ) -> tuple[list[tuple[int, str]], str | None]:
        assert root == app_root
        assert current_pid == 5844
        return [(9001, str(executable))], None

    def _unexpected_schedule(*_args: object, **_kwargs: object) -> tuple[bool, str | None]:
        nonlocal scheduled
        scheduled = True
        return True, None

    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [data_dir])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.sys.executable", str(executable))
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.os.getpid", lambda: 5844)
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.windows_processes_using_tree", _running_processes
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.schedule_windows_managed_cleanup",
        _unexpected_schedule,
    )

    rc = run_uninstall(yes=True)

    captured = capsys.readouterr()
    assert rc == 1
    assert "another OpenSRE process" in captured.err
    assert "PID 9001" in captured.err
    assert "Nothing was deleted" in captured.err
    assert not scheduled
    assert executable.is_file()
    assert launcher.is_file()
    assert data_dir.is_dir()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process scan only")
def test_windows_process_scan_reports_incomplete_enumeration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    real_run = subprocess.run
    monkeypatch.setenv(POWERSHELL_MODULE_PATH_ENV.upper(), r"C:\Program Files\PowerShell\7\Modules")
    monkeypatch.setenv("OPENSRE_TEST_PARENT_VALUE", "preserved")

    def _run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        child_env = kwargs.get("env")
        assert isinstance(child_env, dict)
        assert not any(
            name.casefold() == POWERSHELL_MODULE_PATH_ENV.casefold() for name in child_env
        )
        assert child_env["OPENSRE_TEST_PARENT_VALUE"] == "preserved"
        injected_args = list(args)
        command_index = injected_args.index("-Command") + 1
        injected_args[command_index] = _inject_failed_process_enumerator(
            injected_args[command_index],
            preference="'Stop'",
        )
        return real_run(injected_args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.processes.subprocess.run", _run)

    running, error = windows_processes_using_tree(tmp_path, current_pid=os.getpid())

    assert running == []
    assert error == "could not verify every running OpenSRE process path"


def test_windows_uninstall_refuses_incomplete_process_scan_before_deleting_data(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "incomplete process scan install"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "build-1"
    (version_dir / "_internal").mkdir(parents=True)
    executable = version_dir / "opensre.exe"
    executable.write_bytes(b"MZ")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    data_dir = tmp_path / "incomplete process scan data"
    data_dir.mkdir()
    (data_dir / "state.json").write_text("keep", encoding="utf-8")

    def _unexpected_schedule(*_args: object, **_kwargs: object) -> tuple[bool, str | None]:
        raise AssertionError("an unverifiable process scan must not schedule cleanup")

    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [data_dir])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.sys.executable", str(executable))
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.windows_processes_using_tree",
        lambda _root, **_kwargs: ([], "could not verify every running OpenSRE process path"),
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.schedule_windows_managed_cleanup",
        _unexpected_schedule,
    )

    rc = run_uninstall(yes=True)

    captured = capsys.readouterr()
    assert rc == 1
    assert "could not verify every running OpenSRE process path" in captured.err
    assert "Nothing was deleted" in captured.err
    assert launcher.is_file()
    assert executable.is_file()
    assert (data_dir / "state.json").read_text(encoding="utf-8") == "keep"


def test_windows_uninstall_rechecks_processes_after_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "prompt race install"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "build-1"
    (version_dir / "_internal").mkdir(parents=True)
    executable = version_dir / "opensre.exe"
    executable.write_bytes(b"MZ")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    data_dir = tmp_path / "prompt race data"
    data_dir.mkdir()
    confirmed = False

    def _ask(_self: object) -> bool:
        nonlocal confirmed
        confirmed = True
        return True

    def _confirm(*_args: object, **_kwargs: object) -> object:
        return type("Confirmation", (), {"ask": _ask})()

    def _running_processes(
        root: Path, *, current_pid: int
    ) -> tuple[list[tuple[int, str]], str | None]:
        assert confirmed
        assert root == app_root
        assert current_pid == 5844
        return [(9002, str(executable))], None

    def _unexpected_schedule(*_args: object, **_kwargs: object) -> tuple[bool, str | None]:
        raise AssertionError("busy layout must not schedule cleanup")

    import questionary as _q

    monkeypatch.setattr(_q, "confirm", _confirm)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [data_dir])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.sys.executable", str(executable))
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.os.getpid", lambda: 5844)
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.windows_processes_using_tree", _running_processes
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.schedule_windows_managed_cleanup",
        _unexpected_schedule,
    )

    rc = run_uninstall(yes=False)

    captured = capsys.readouterr()
    assert rc == 1
    assert "PID 9002" in captured.err
    assert "Nothing was deleted" in captured.err
    assert launcher.is_file()
    assert executable.is_file()
    assert data_dir.is_dir()


def test_run_uninstall_windows_layout_schedules_owned_paths_after_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "install dir"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "build-1"
    (version_dir / "_internal").mkdir(parents=True)
    executable = version_dir / "opensre.exe"
    executable.write_bytes(b"MZ")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    install_lock = install_dir / ".opensre-app.install.lock"
    install_lock.write_bytes(b"")
    legacy_executable = install_dir / "opensre.exe"
    legacy_executable.write_bytes(b"MZ")
    unrelated = install_dir / "keep-me.txt"
    unrelated.write_text("keep", encoding="utf-8")
    data_dir = tmp_path / "user data"
    data_dir.mkdir()
    data_file = data_dir / "state.json"
    data_file.write_text("keep until worker succeeds", encoding="utf-8")
    scheduled: list[dict[str, object]] = []

    def _schedule(**kwargs: object) -> tuple[bool, str | None]:
        scheduled.append(kwargs)
        return True, None

    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [data_dir])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.sys.executable", str(executable))
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.os.getpid", lambda: 731)
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.windows_processes_using_tree",
        lambda _root, **_kwargs: ([], None),
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.schedule_windows_managed_cleanup", _schedule
    )

    rc = run_uninstall(yes=True)

    assert rc == 0
    assert scheduled == [
        {
            "executable": executable,
            "app_root": app_root,
            "launcher": launcher,
            "parent_pid": 731,
            "data_paths": [data_dir],
        }
    ]
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert data_file.read_text(encoding="utf-8") == "keep until worker succeeds"
    assert legacy_executable.read_bytes() == b"MZ"
    assert executable.exists()
    output = capsys.readouterr().out
    assert "after this process exits" in output
    assert "after binary cleanup succeeds" in output


def test_windows_cleanup_launch_failure_preserves_data_and_installation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "cleanup launch failure"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "build-1"
    (version_dir / "_internal").mkdir(parents=True)
    executable = version_dir / "opensre.exe"
    executable.write_bytes(b"MZ")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    data_dir = tmp_path / "cleanup launch data"
    data_dir.mkdir()
    (data_dir / "state.json").write_text("keep", encoding="utf-8")

    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [data_dir])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.sys.executable", str(executable))
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.windows_processes_using_tree",
        lambda _root, **_kwargs: ([], None),
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.uninstall.schedule_windows_managed_cleanup",
        lambda **_kwargs: (False, "forced launch failure"),
    )

    rc = run_uninstall(yes=True)

    captured = capsys.readouterr()
    assert rc == 1
    assert "could not schedule binary cleanup" in captured.err
    assert "Nothing was deleted" in captured.err
    assert launcher.is_file()
    assert executable.is_file()
    assert (data_dir / "state.json").read_text(encoding="utf-8") == "keep"


def test_run_uninstall_windows_legacy_binary_defers_exact_executable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "legacy install" / "opensre.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"MZ")
    data_dir = tmp_path / "legacy data"
    data_dir.mkdir()
    scheduled: list[dict[str, object]] = []

    def _schedule(
        paths: list[Path],
        *,
        parent_pid: int,
        data_paths: list[Path] | None = None,
        install_lock_path: Path | None = None,
        data_guard_paths: list[Path] | None = None,
    ) -> tuple[bool, str | None]:
        assert parent_pid == 812
        scheduled.append(
            {
                "paths": paths,
                "data_paths": data_paths,
                "install_lock_path": install_lock_path,
                "data_guard_paths": data_guard_paths,
            }
        )
        return True, None

    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [data_dir])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_windows", lambda: True)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.sys.executable", str(executable))
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.os.getpid", lambda: 812)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall.schedule_windows_cleanup", _schedule)

    rc = run_uninstall(yes=True)

    assert rc == 0
    assert scheduled == [
        {
            "paths": [executable],
            "data_paths": [data_dir],
            "install_lock_path": executable.parent / ".opensre-app.install.lock",
            "data_guard_paths": [
                executable.parent / ".opensre-app",
                executable.parent / "opensre.cmd",
                executable,
            ],
        }
    ]
    assert executable.exists()
    assert data_dir.is_dir()


def test_schedule_windows_cleanup_uses_hidden_background_powershell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "path with spaces" / "opensre.cmd"
    target.parent.mkdir()
    target.write_bytes(b"MZ-legacy")
    missing_target = target.with_name("missing.exe")
    install_lock = target.parent / ".opensre-app.install.lock"
    install_lock.write_bytes(b"")
    captured: dict[str, object] = {}
    monkeypatch.setenv(POWERSHELL_MODULE_PATH_ENV.upper(), r"C:\Program Files\PowerShell\7\Modules")
    monkeypatch.setenv("OPENSRE_TEST_PARENT_VALUE", "preserved")

    def _popen(args: list[str], **kwargs: object) -> object:
        captured["args"] = args
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.powershell.windows_powershell_executable",
        lambda: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    )
    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.subprocess.Popen", _popen)
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )

    ok, err = schedule_windows_cleanup(
        [target, missing_target],
        parent_pid=934,
        install_lock_path=install_lock,
    )

    assert ok is True
    assert err is None
    args = captured["args"]
    assert isinstance(args, list)
    assert "-WindowStyle" in args
    assert "Hidden" in args
    file_index = args.index("-File")
    cleanup_path = Path(args[file_index + 1])
    try:
        script = cleanup_path.read_text(encoding="utf-8-sig")
        assert "param(" in script
        assert "Move-OpenSreTargetIfUnused" in script
        assert "[System.IO.FileMode]::OpenOrCreate" not in script
        assert "GetIdentityFromHandle($Handle)" in script
        assert "MarkDeleteOnClose($lockHandle)" in script
        assert "Remove-Item -LiteralPath $deleteLockPath" not in script
        assert "(ConvertTo-OpenSreExtendedPath -Path $cleanupLockPath)" in script
        parent_index = args.index("-ParentProcessId")
        assert args[parent_index + 1] == "934"
        payload_index = args.index("-CleanupPayload")
        payload = json.loads(base64.b64decode(args[payload_index + 1]))
        assert len(payload["operation_id"]) == 32
        assert payload["parent"] == {
            "pid": 934,
            "path": str(Path(sys.executable).resolve()),
            "started_filetime_utc": 1,
        }
        target_metadata = target.stat(follow_symlinks=False)
        assert payload["targets"] == [
            {
                "path": str(target),
                "kind": "file",
                "sha256": hashlib.sha256(b"MZ-legacy").hexdigest(),
                "volume_serial_number": int(target_metadata.st_dev) & 0xFFFFFFFF,
                "file_index": int(target_metadata.st_ino) & 0xFFFFFFFFFFFFFFFF,
                "creation_filetime_utc": (
                    target_metadata.st_ctime_ns // 100 + 116_444_736_000_000_000
                ),
            },
            {"path": str(missing_target), "kind": "missing", "sha256": ""},
        ]
        assert payload["managed"] is None
        assert payload["data_targets"] == []
        lock_metadata = install_lock.stat(follow_symlinks=False)
        assert payload["lock"] == {
            "path": str(install_lock.resolve()),
            "volume_serial_number": int(lock_metadata.st_dev) & 0xFFFFFFFF,
            "file_index": int(lock_metadata.st_ino) & 0xFFFFFFFFFFFFFFFF,
            "creation_filetime_utc": (lock_metadata.st_ctime_ns // 100 + 116_444_736_000_000_000),
        }
        assert payload["data_guard_paths"] == []
        cleanup_index = args.index("-CleanupScriptPath")
        assert Path(args[cleanup_index + 1]) == cleanup_path
    finally:
        cleanup_path.unlink(missing_ok=True)
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL
    child_env = captured["env"]
    assert isinstance(child_env, dict)
    assert not any(name.casefold() == POWERSHELL_MODULE_PATH_ENV.casefold() for name in child_env)
    assert child_env["OPENSRE_TEST_PARENT_VALUE"] == "preserved"
    assert Path(str(captured["cwd"])) == cleanup_path.parent
    assert isinstance(captured["creationflags"], int)
    assert captured["creationflags"] != 0


def test_schedule_windows_cleanup_removes_new_lock_when_launch_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "opensre.exe"
    target.write_bytes(b"MZ")
    install_lock = tmp_path / ".opensre-app.install.lock"

    def _fail_launch(*_args: object, **_kwargs: object) -> None:
        raise OSError("forced cleanup launch failure")

    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.subprocess.Popen", _fail_launch)

    ok, error = schedule_windows_cleanup(
        [target],
        parent_pid=934,
        install_lock_path=install_lock,
    )

    assert ok is False
    assert error == "forced cleanup launch failure"
    assert not install_lock.exists()


def test_schedule_windows_cleanup_removes_new_lock_when_final_path_mismatches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import surfaces.cli.lifecycle.windows.cleanup as cleanup_module

    target = tmp_path / "opensre.exe"
    target.write_bytes(b"MZ")
    install_lock = tmp_path / ".opensre-app.install.lock"
    real_same_windows_path = cleanup_module._same_windows_path

    def _mismatch_only_lock(left: Path, right: Path) -> bool:
        if left.name == install_lock.name or right.name == install_lock.name:
            return False
        return real_same_windows_path(left, right)

    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup._same_windows_path",
        _mismatch_only_lock,
    )

    ok, error = schedule_windows_cleanup(
        [target],
        parent_pid=934,
        install_lock_path=install_lock,
    )

    assert ok is False
    assert error == "Windows cleanup lock resolved outside its install directory"
    assert not install_lock.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
def test_schedule_windows_cleanup_removes_path_after_parent_exit(tmp_path: Path) -> None:
    short_target = tmp_path / "a"
    payload_dir = short_target
    while len(str(payload_dir / "payload.txt")) <= 220:
        payload_dir /= "nested-content-filter"
    payload_dir.mkdir(parents=True)
    payload = payload_dir / "payload.txt"
    payload.write_text("temporary", encoding="utf-8")
    relative_payload = payload.relative_to(short_target)
    target = tmp_path / ("path with spaces-" + ("x" * 50))
    short_target.rename(target)
    assert len(str(target / relative_payload)) > 260
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )

    try:
        ok, err = schedule_windows_cleanup([target], parent_pid=holder.pid)
        assert ok is True, err
        assert err is None
        assert target.exists()

        holder.terminate()
        holder.wait(timeout=10)
        deadline = time.monotonic() + 90
        while target.exists() and time.monotonic() < deadline:
            time.sleep(0.1)

        assert not target.exists()
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
@pytest.mark.parametrize(
    ("has_exited", "readable_metadata", "expected_exit", "target_removed"),
    [
        ("true", False, 0, True),
        ("false-then-true", False, 0, True),
        ("false-then-true", True, 0, True),
        ("false", False, 1, False),
        ("non-bool", False, 1, False),
        ("throw", False, 1, False),
    ],
    ids=(
        "confirmed-exit",
        "exit-during-metadata-error",
        "exit-after-readable-metadata",
        "still-running",
        "non-boolean",
        "inspection-error",
    ),
)
def test_cleanup_worker_parent_process_state_requires_confirmed_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    has_exited: str,
    readable_metadata: bool,
    expected_exit: int,
    target_removed: bool,
) -> None:
    target = tmp_path / f"parent-metadata-{has_exited}.txt"
    target.write_text("candidate", encoding="utf-8")
    install_lock = tmp_path / f"parent-metadata-{has_exited}.lock"
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_parent_process_state(
            read_cleanup_script(),
            has_exited=has_exited,
            readable_metadata=readable_metadata,
        ),
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    real_popen = subprocess.Popen
    workers: list[subprocess.Popen[bytes]] = []

    def _capture_worker(args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        if "-CleanupScriptPath" not in args:
            return real_popen(args, **kwargs)
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        worker = real_popen(args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.subprocess.Popen", _capture_worker)

    try:
        ok, error = schedule_windows_cleanup(
            [target],
            parent_pid=2_147_483_647,
            install_lock_path=install_lock,
        )
        assert ok is True, error
        assert len(workers) == 1
        output, _ = workers[0].communicate(timeout=90)
        assert workers[0].returncode == expected_exit, output.decode("utf-8", errors="replace")
        assert target.exists() is (not target_removed)
        assert install_lock.exists() is (not target_removed)
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
            worker.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
@pytest.mark.parametrize(
    (
        "process_name",
        "process_path",
        "has_exited",
        "include_busy_process",
        "expected_exit",
        "target_removed",
    ),
    [
        ("opensre", "throw", "false-then-true", False, 0, True),
        ("opensre", "throw", "false-then-true", True, 1, False),
        ("opensre", "target", "false-then-true", False, 0, True),
        ("opensre", "throw", "false", False, 1, False),
        ("opensre", "throw", "throw", False, 1, False),
        ("empty", "throw", "false", False, 1, False),
        ("throw", "throw", "true", False, 0, True),
    ],
    ids=(
        "exit-during-path-inspection-is-skipped",
        "later-busy-entry-still-blocks",
        "exit-after-path-comparison-is-skipped",
        "inaccessible-live-entry-fails-closed",
        "unknown-exit-state-fails-closed",
        "empty-name-fails-closed",
        "exited-entry-with-unreadable-name-is-skipped",
    ),
)
def test_cleanup_worker_scan_requires_confirmed_process_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    process_name: str,
    process_path: str,
    has_exited: str,
    include_busy_process: bool,
    expected_exit: int,
    target_removed: bool,
) -> None:
    target = tmp_path / f"scan-process-{has_exited}-{include_busy_process}.txt"
    target.write_text("candidate", encoding="utf-8")
    install_lock = tmp_path / f"scan-process-{has_exited}-{include_busy_process}.lock"
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_scan_process_state(
            read_cleanup_script(),
            target=target,
            process_name=process_name,
            process_path=process_path,
            has_exited=has_exited,
            include_busy_process=include_busy_process,
        ),
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    workers = _capture_cleanup_workers(monkeypatch)

    try:
        ok, error = schedule_windows_cleanup(
            [target],
            parent_pid=2_147_483_647,
            install_lock_path=install_lock,
        )
        assert ok is True, error
        assert len(workers) == 1
        output, _ = workers[0].communicate(timeout=90)
        assert workers[0].returncode == expected_exit, output.decode("utf-8", errors="replace")
        assert target.exists() is (not target_removed)
        assert install_lock.exists() is (not target_removed)
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
            worker.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
def test_cleanup_worker_parent_lookup_failure_is_not_treated_as_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "parent-lookup-failure.txt"
    target.write_text("candidate", encoding="utf-8")
    install_lock = tmp_path / "parent-lookup-failure.lock"
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_parent_lookup_failure(read_cleanup_script()),
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    real_popen = subprocess.Popen
    workers: list[subprocess.Popen[bytes]] = []

    def _capture_worker(args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        if "-CleanupScriptPath" not in args:
            return real_popen(args, **kwargs)
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        worker = real_popen(args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.subprocess.Popen", _capture_worker)

    try:
        ok, error = schedule_windows_cleanup(
            [target],
            parent_pid=2_147_483_647,
            install_lock_path=install_lock,
        )
        assert ok is True, error
        assert len(workers) == 1
        output, _ = workers[0].communicate(timeout=90)
        assert workers[0].returncode == 1, output.decode("utf-8", errors="replace")
        assert target.read_text(encoding="utf-8") == "candidate"
        assert install_lock.exists()
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
            worker.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
def test_cleanup_worker_resolves_short_path_parent_and_busy_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tests.cli.test_install_ps1_onedir import _fake_opensre_executable

    fake_opensre = _fake_opensre_executable()
    process_root = Path(
        tempfile.mkdtemp(prefix="opensre-short-path-process-", dir=tmp_path.resolve(strict=True))
    )
    parent_dir = process_root / "Long Parent Directory"
    busy_root = process_root / "Long Busy Bundle Directory"
    parent_dir.mkdir()
    busy_root.mkdir()
    parent_executable = parent_dir / "opensre.exe"
    busy_executable = busy_root / "opensre.exe"
    shutil.copy2(fake_opensre, parent_executable)
    shutil.copy2(fake_opensre, busy_executable)
    short_parent = _short_path_or_skip(parent_executable)
    short_busy = _short_path_or_skip(busy_executable)
    sentinel = tmp_path / "wait-for-the-real-parent.txt"
    sentinel.write_text("remove only after parent exit", encoding="utf-8")
    install_lock = tmp_path / "short-path-cleanup.lock"
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    user_data = tmp_path / "user-data" / "state.json"
    user_data.parent.mkdir()
    user_data.write_text("keep", encoding="utf-8")
    workers: list[subprocess.Popen[bytes]] = []
    child_handles: dict[str, int] = {}
    release_parent = process_root / "release-parent"
    release_busy = process_root / "release-busy"
    failure_details: dict[str, Any] | None = None
    with tempfile.TemporaryFile() as worker_output:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.TerminateProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_wchar_p,
        ]
        kernel32.CreateEventW.restype = ctypes.c_void_p
        kernel32.SetEvent.argtypes = [ctypes.c_void_p]
        kernel32.SetEvent.restype = ctypes.c_int
        observed_name = f"Local\\{process_root.name}-parent-observed"
        resume_name = f"Local\\{process_root.name}-resume-inspection"
        observed_event = kernel32.CreateEventW(None, True, False, observed_name)
        resume_event = kernel32.CreateEventW(None, True, False, resume_name)
        if not observed_event or not resume_event:
            for event in (observed_event, resume_event):
                if event:
                    kernel32.CloseHandle(event)
            raise ctypes.WinError(ctypes.get_last_error())

        def _retain_child(name: str, pid: int) -> None:
            handle = kernel32.OpenProcess(0x00100001, False, pid)  # SYNCHRONIZE | TERMINATE
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            child_handles[name] = handle

        def _failure_state() -> dict[str, Any]:
            return {
                "parent_pid": parent_pid,
                "busy_pid": busy_pid,
                "workers": [{"pid": worker.pid, "exit_code": worker.poll()} for worker in workers],
                "child_wait_status": {
                    name: kernel32.WaitForSingleObject(handle, 0)
                    for name, handle in child_handles.items()
                },
                "sentinel_exists": sentinel.exists(),
                "installation_exists": busy_root.exists(),
                "busy_executable_exists": busy_executable.exists(),
                "lock_exists": install_lock.exists(),
                "user_data_exists": user_data.exists(),
                "unrelated_exists": unrelated.exists(),
                "quarantines": [str(path) for path in tmp_path.glob("*.uninstall-*")],
            }

        def _synchronized_source() -> str:
            source = read_cleanup_script()
            anchor = "            $parent = Get-Process -Id $ParentProcessId -ErrorAction Stop\n"
            assert source.count(anchor) == 1
            handshake = f"""
            if ($null -ne $parent -and -not $script:parentObserved) {{
                $script:parentObserved = $true
                $null = $parent.Handle
                $observed = [System.Threading.EventWaitHandle]::OpenExisting('{observed_name}')
                $resume = [System.Threading.EventWaitHandle]::OpenExisting('{resume_name}')
                try {{
                    $null = $observed.Set()
                    if (-not $resume.WaitOne(30000)) {{
                        throw 'Parent inspection handshake timed out'
                    }}
                }}
                finally {{ $observed.Dispose(); $resume.Dispose() }}
            }}
    """
            return source.replace(anchor, anchor + handshake, 1)

        real_popen = subprocess.Popen

        def _capture_worker(args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
            kwargs.update(stdout=worker_output, stderr=subprocess.STDOUT)
            worker = real_popen(args, **kwargs)
            workers.append(worker)
            return worker

        created_cleanup_scripts: list[Path] = []
        real_mkstemp = tempfile.mkstemp

        def _mkstemp(*, prefix: str, suffix: str) -> tuple[int, str]:
            descriptor, name = real_mkstemp(prefix=prefix, suffix=suffix, dir=tmp_path)
            created_cleanup_scripts.append(Path(name))
            return descriptor, name

        parent_pid = 0
        busy_pid = 0

        try:
            parent_pid = _start_hidden_windows_process(short_parent, release_parent)
            _retain_child("parent", parent_pid)
            busy_pid = _start_hidden_windows_process(short_busy, release_busy)
            _retain_child("busy", busy_pid)
            parent_identity, identity_error = windows_process_identity(
                parent_pid,
                expected_executable=parent_executable,
            )
            assert parent_identity is not None, identity_error

            def _parent_identity(
                pid: int, *, expected_executable: Path | None = None
            ) -> tuple[WindowsProcessIdentity, None]:
                assert pid == parent_pid
                assert expected_executable is not None
                return parent_identity, None

            monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.tempfile.mkstemp", _mkstemp)
            monkeypatch.setattr(
                "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
                _parent_identity,
            )

            with monkeypatch.context() as capture:
                capture.setattr(
                    "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
                    _synchronized_source,
                )
                capture.setattr(
                    "surfaces.cli.lifecycle.windows.cleanup.subprocess.Popen", _capture_worker
                )
                ok, error = schedule_windows_cleanup(
                    [sentinel, busy_root],
                    parent_pid=parent_pid,
                    install_lock_path=install_lock,
                )
            assert ok is True, error
            assert len(created_cleanup_scripts) == 1

            ready_status = kernel32.WaitForSingleObject(observed_event, 90_000)
            assert ready_status == 0
            running_parent, parent_error = windows_process_identity(
                parent_pid,
                expected_executable=parent_executable,
            )
            assert running_parent is not None, parent_error
            assert sentinel.read_text(encoding="utf-8") == "remove only after parent exit"

            release_parent.write_text("release", encoding="utf-8")
            assert kernel32.WaitForSingleObject(child_handles["parent"], 10_000) == 0
            assert kernel32.SetEvent(resume_event)
            cleanup_script = created_cleanup_scripts[0]
            assert len(workers) == 1
            workers[0].wait(timeout=90)
            assert workers[0].returncode == 1
            assert install_lock.exists()
            assert not cleanup_script.exists()
            assert sentinel.read_text(encoding="utf-8") == "remove only after parent exit"
            running_busy, busy_error = windows_process_identity(
                busy_pid,
                expected_executable=busy_executable,
            )
            assert running_busy is not None, busy_error
            assert busy_executable.is_file()
            assert unrelated.read_text(encoding="utf-8") == "keep"
            assert user_data.read_text(encoding="utf-8") == "keep"
        except Exception as exc:
            failure_details = {"error": repr(exc), "before_teardown": _failure_state()}
            raise
        finally:
            kernel32.SetEvent(resume_event)
            teardown_errors: list[str] = []
            release_parent.write_text("release", encoding="utf-8")
            release_busy.write_text("release", encoding="utf-8")
            for worker in workers:
                try:
                    if worker.poll() is None:
                        worker.terminate()
                    worker.wait(timeout=10)
                except Exception as exc:
                    teardown_errors.append(repr(exc))
            for name, handle in child_handles.items():
                if kernel32.WaitForSingleObject(handle, 10_000) != 0:
                    kernel32.TerminateProcess(handle, 1)
                    if kernel32.WaitForSingleObject(handle, 10_000) != 0:
                        teardown_errors.append(f"{name} did not terminate")
            try:
                if failure_details is not None or teardown_errors:
                    details = {
                        "failure": failure_details,
                        "after_teardown": _failure_state(),
                        "teardown_errors": teardown_errors,
                    }
                    (tmp_path / "cleanup-worker-failure.json").write_text(
                        json.dumps(details, indent=2), encoding="utf-8"
                    )
                    worker_output.seek(0)
                    (tmp_path / "cleanup-worker-failure.log").write_bytes(worker_output.read())
            finally:
                for handle in child_handles.values():
                    kernel32.CloseHandle(handle)
                kernel32.CloseHandle(observed_event)
                kernel32.CloseHandle(resume_event)
            assert not teardown_errors, teardown_errors


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction regression")
def test_schedule_windows_cleanup_rejects_target_parent_swap_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import surfaces.cli.lifecycle.windows.cleanup as cleanup_module

    install_parent = tmp_path / "target-race-parent"
    install_parent.mkdir()
    target = install_parent / "opensre.exe"
    target.write_bytes(b"MZ-same-content")
    preserved_parent = tmp_path / "target-race-preserved"
    outside_parent = tmp_path / "target-race-outside"
    outside_parent.mkdir()
    outside_target = outside_parent / target.name
    outside_target.write_bytes(target.read_bytes())
    outside_sentinel = outside_parent / "unrelated.txt"
    outside_sentinel.write_text("preserve", encoding="utf-8")
    install_lock = install_parent / ".opensre-app.install.lock"
    real_canonical_existing_path = cleanup_module.canonical_existing_path
    swapped = False
    restored = False
    worker_launched = False

    def _canonical_with_swap_back(path: Path) -> Path:
        nonlocal restored, swapped
        if Path(os.path.abspath(path)) != target or swapped:
            return real_canonical_existing_path(path)
        install_parent.rename(preserved_parent)
        linked = subprocess.run(
            [
                os.environ["COMSPEC"],
                "/d",
                "/c",
                "mklink",
                "/J",
                str(install_parent),
                str(outside_parent),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert linked.returncode == 0, linked.stdout + linked.stderr
        swapped = True
        canonical = real_canonical_existing_path(path)
        install_parent.rmdir()
        preserved_parent.rename(install_parent)
        restored = True
        return canonical

    real_popen = subprocess.Popen

    def _unexpected_worker(args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        nonlocal worker_launched
        if "-CleanupScriptPath" not in args:
            return real_popen(args, **kwargs)
        worker_launched = True
        raise OSError("an ownership-mismatched target must not launch cleanup")

    monkeypatch.setattr(cleanup_module, "canonical_existing_path", _canonical_with_swap_back)
    monkeypatch.setattr(cleanup_module, "windows_process_identity", _missing_process_identity)
    monkeypatch.setattr(cleanup_module.subprocess, "Popen", _unexpected_worker)

    try:
        ok, error = schedule_windows_cleanup(
            [target],
            parent_pid=934,
            install_lock_path=install_lock,
        )

        assert ok is False
        assert error == (
            f"Windows cleanup path resolved away from its classified location: {target}"
        )
        assert swapped is True
        assert restored is True
        assert worker_launched is False
        assert target.read_bytes() == b"MZ-same-content"
        assert outside_target.read_bytes() == b"MZ-same-content"
        assert outside_sentinel.read_text(encoding="utf-8") == "preserve"
        assert not install_lock.exists()
        assert not (outside_parent / install_lock.name).exists()
    finally:
        if install_parent.is_junction():
            install_parent.rmdir()
        if preserved_parent.exists() and not install_parent.exists():
            preserved_parent.rename(install_parent)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction regression")
def test_schedule_windows_managed_cleanup_rejects_app_parent_swap_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import surfaces.cli.lifecycle.windows.cleanup as cleanup_module

    def _managed_app(install_dir: Path, payload: bytes) -> tuple[Path, Path, Path]:
        app_root = install_dir / ".opensre-app"
        version_dir = app_root / "versions" / "build-1"
        version_dir.mkdir(parents=True)
        executable = version_dir / "opensre.exe"
        executable.write_bytes(payload)
        (app_root / "layout-v1.marker").write_text(
            "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
        )
        (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
        launcher = install_dir / "opensre.cmd"
        launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
        return app_root, executable, launcher

    install_dir = tmp_path / "managed-target-race"
    app_root, executable, launcher = _managed_app(install_dir, b"MZ-same-content")
    preserved_app_root = tmp_path / "managed-target-preserved"
    outside_install_dir = tmp_path / "managed-target-outside"
    outside_app_root, outside_executable, outside_launcher = _managed_app(
        outside_install_dir, b"MZ-same-content"
    )
    outside_sentinel = outside_install_dir / "unrelated.txt"
    outside_sentinel.write_text("preserve", encoding="utf-8")
    install_lock = install_dir / ".opensre-app.install.lock"
    real_canonical_existing_path = cleanup_module.canonical_existing_path
    swapped = False
    restored = False
    worker_launched = False

    def _restore_app_root() -> None:
        nonlocal restored
        if app_root.is_junction():
            app_root.rmdir()
        if preserved_app_root.exists() and not app_root.exists():
            preserved_app_root.rename(app_root)
        if swapped:
            restored = True

    def _canonical_with_swap_back(path: Path) -> Path:
        nonlocal swapped
        absolute = Path(os.path.abspath(path))
        if absolute == app_root and not swapped:
            app_root.rename(preserved_app_root)
            linked = subprocess.run(
                [
                    os.environ["COMSPEC"],
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(app_root),
                    str(outside_app_root),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert linked.returncode == 0, linked.stdout + linked.stderr
            swapped = True
            return real_canonical_existing_path(path)
        if absolute == executable and swapped and not restored:
            canonical = real_canonical_existing_path(path)
            _restore_app_root()
            return canonical
        return real_canonical_existing_path(path)

    real_popen = subprocess.Popen

    def _unexpected_worker(args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        nonlocal worker_launched
        if "-CleanupScriptPath" not in args:
            return real_popen(args, **kwargs)
        worker_launched = True
        raise OSError("an ownership-mismatched managed target must not launch cleanup")

    monkeypatch.setattr(cleanup_module, "canonical_existing_path", _canonical_with_swap_back)
    monkeypatch.setattr(cleanup_module, "windows_process_identity", _missing_process_identity)
    monkeypatch.setattr(cleanup_module.subprocess, "Popen", _unexpected_worker)

    try:
        try:
            ok, error = schedule_windows_managed_cleanup(
                executable=executable,
                app_root=app_root,
                launcher=launcher,
                parent_pid=934,
            )
        finally:
            _restore_app_root()

        assert ok is False
        assert error == (
            f"Windows cleanup path resolved away from its classified location: {app_root}"
        )
        assert swapped is True
        assert restored is True
        assert worker_launched is False
        assert executable.read_bytes() == b"MZ-same-content"
        assert outside_executable.read_bytes() == b"MZ-same-content"
        assert launcher.read_text(encoding="utf-8").startswith("@echo off")
        assert outside_launcher.read_text(encoding="utf-8").startswith("@echo off")
        assert outside_sentinel.read_text(encoding="utf-8") == "preserve"
        assert not install_lock.exists()
        assert not (outside_install_dir / install_lock.name).exists()
    finally:
        _restore_app_root()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction regression")
def test_schedule_windows_cleanup_rejects_lock_parent_swap_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import surfaces.cli.lifecycle.windows.cleanup as cleanup_module

    install_parent = tmp_path / "lock-race-parent"
    install_parent.mkdir()
    target = install_parent / "opensre.exe"
    target.write_bytes(b"MZ")
    preserved_parent = tmp_path / "lock-race-preserved"
    outside = tmp_path / "lock-race-outside"
    outside.mkdir()
    install_lock = install_parent / ".opensre-app.install.lock"
    outside_lock = outside / install_lock.name
    real_canonical_existing_path = cleanup_module.canonical_existing_path
    real_windows_handle_identity = cleanup_module._windows_handle_identity
    swapped = False
    restored = False

    def _canonical_with_swap(path: Path) -> Path:
        nonlocal swapped
        canonical = real_canonical_existing_path(path)
        if Path(os.path.abspath(path)) == install_parent and not swapped:
            install_parent.rename(preserved_parent)
            linked = subprocess.run(
                [
                    os.environ["COMSPEC"],
                    "/d",
                    "/c",
                    "mklink",
                    "/J",
                    str(install_parent),
                    str(outside),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert linked.returncode == 0, linked.stdout + linked.stderr
            monkeypatch.setattr(cleanup_module.subprocess, "Popen", _unexpected_worker)
            swapped = True
        return canonical

    def _identity_with_swap_back(
        handle: int, path: Path, *, allow_directory: bool = False
    ) -> tuple[Path, int, int, int]:
        nonlocal restored
        identity = real_windows_handle_identity(handle, path, allow_directory=allow_directory)
        if swapped and not restored:
            assert install_parent.is_junction()
            install_parent.rmdir()
            preserved_parent.rename(install_parent)
            restored = True
        return identity

    def _unexpected_worker(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an identity-mismatched lock must not launch cleanup")

    monkeypatch.setattr(cleanup_module, "canonical_existing_path", _canonical_with_swap)
    monkeypatch.setattr(cleanup_module, "_windows_handle_identity", _identity_with_swap_back)
    monkeypatch.setattr(cleanup_module, "windows_process_identity", _missing_process_identity)

    try:
        ok, error = schedule_windows_cleanup(
            [target],
            parent_pid=934,
            install_lock_path=install_lock,
        )

        assert ok is False
        assert error == "Windows cleanup lock resolved outside its install directory"
        assert swapped is True
        assert restored is True
        assert target.read_bytes() == b"MZ"
        assert not install_lock.exists()
        assert not outside_lock.exists()
    finally:
        if install_parent.is_junction():
            install_parent.rmdir()
        if preserved_parent.exists() and not install_parent.exists():
            preserved_parent.rename(install_parent)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction regression")
def test_cleanup_worker_never_path_deletes_lock_after_final_parent_swap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_parent = tmp_path / "final-lock-parent"
    install_parent.mkdir()
    install_lock = install_parent / ".opensre-app.install.lock"
    install_lock.write_bytes(b"verified-lock")
    preserved_parent = tmp_path / "final-lock-preserved"
    outside_parent = tmp_path / "final-lock-outside"
    outside_parent.mkdir()
    outside_lock = outside_parent / install_lock.name
    outside_lock.write_bytes(b"unrelated-lock")
    outside_sentinel = outside_parent / "sentinel.txt"
    outside_sentinel.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_final_lock_parent_swap(
            _inject_empty_process_enumerator(read_cleanup_script()),
            install_parent=install_parent,
            preserved_parent=preserved_parent,
            outside_parent=outside_parent,
        ),
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    cleanup_scripts: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def _mkstemp(*, prefix: str, suffix: str) -> tuple[int, str]:
        descriptor, name = real_mkstemp(prefix=prefix, suffix=suffix, dir=tmp_path)
        cleanup_scripts.append(Path(name))
        return descriptor, name

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.tempfile.mkstemp", _mkstemp)
    real_popen = subprocess.Popen
    workers: list[subprocess.Popen[bytes]] = []

    def _capture_worker(args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        if "-CleanupScriptPath" not in args:
            return real_popen(args, **kwargs)
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        worker = real_popen(args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.subprocess.Popen", _capture_worker)

    try:
        ok, error = schedule_windows_cleanup(
            [],
            parent_pid=2_147_483_647,
            install_lock_path=install_lock,
        )

        assert ok is True, error
        assert len(workers) == 1
        assert len(cleanup_scripts) == 1
        output, _ = workers[0].communicate(timeout=90)
        assert workers[0].returncode == 0, output.decode("utf-8", errors="replace")
        assert not cleanup_scripts[0].exists()
        assert install_parent.is_junction()
        assert (preserved_parent / install_lock.name).read_bytes() == b"verified-lock"
        assert outside_lock.read_bytes() == b"unrelated-lock"
        assert outside_sentinel.read_text(encoding="utf-8") == "preserve"
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
            worker.wait(timeout=10)
        if install_parent.is_junction():
            install_parent.rmdir()
        if preserved_parent.exists() and not install_parent.exists():
            preserved_parent.rename(install_parent)
        for cleanup_script in cleanup_scripts:
            cleanup_script.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows long-path regression")
def test_cleanup_worker_opens_boundary_length_lock_with_extended_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent_length = 238
    leaf_length = parent_length - len(str(tmp_path)) - 1
    assert 0 < leaf_length <= 255
    install_parent = tmp_path / ("p" * leaf_length)
    install_parent.mkdir()
    assert len(str(install_parent)) == parent_length
    install_lock = install_parent / ".opensre-app.install.lock"
    assert len(str(install_lock)) > 260
    extended_lock = Path("\\\\?\\" + str(install_lock))
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_empty_process_enumerator(read_cleanup_script()),
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    real_popen = subprocess.Popen
    workers: list[subprocess.Popen[bytes]] = []
    lock_payloads: list[dict[str, object]] = []

    def _capture_worker(args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        payload_index = args.index("-CleanupPayload")
        payload = json.loads(base64.b64decode(args[payload_index + 1]))
        lock_payloads.append(payload["lock"])
        if "-CleanupScriptPath" not in args:
            return real_popen(args, **kwargs)
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        worker = real_popen(args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.subprocess.Popen", _capture_worker)

    try:
        ok, error = schedule_windows_cleanup(
            [],
            parent_pid=2_147_483_647,
            install_lock_path=install_lock,
        )
        assert ok is True, error
        assert len(workers) == 1
        assert len(lock_payloads) == 1
        assert lock_payloads[0]["path"] == str(install_lock)
        assert not str(lock_payloads[0]["path"]).startswith("\\\\?\\")
        output, _ = workers[0].communicate(timeout=90)
        assert workers[0].returncode == 0, output.decode("utf-8", errors="replace")
        assert not extended_lock.exists()
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
            worker.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction regression")
def test_cleanup_worker_rejects_ancestor_junction_swap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_parent = tmp_path / "install-parent"
    install_parent.mkdir()
    target = install_parent / "opensre.exe"
    target.write_bytes(b"MZ-same-content")
    preserved_parent = tmp_path / "preserved-original"
    outside = tmp_path / "outside-user-data"
    outside.mkdir()
    outside_target = outside / "opensre.exe"
    outside_target.write_bytes(target.read_bytes())
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    lock_path = install_parent / ".opensre-app.install.lock"
    outside_lock = outside / lock_path.name
    created_cleanup_scripts: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def _mkstemp(*, prefix: str, suffix: str) -> tuple[int, str]:
        descriptor, name = real_mkstemp(prefix=prefix, suffix=suffix, dir=tmp_path)
        created_cleanup_scripts.append(Path(name))
        return descriptor, name

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.tempfile.mkstemp", _mkstemp)
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )

    try:
        ok, error = schedule_windows_cleanup(
            [target],
            parent_pid=holder.pid,
            install_lock_path=lock_path,
        )
        assert ok is True, error
        assert lock_path.is_file()
        install_parent.rename(preserved_parent)
        assert (preserved_parent / lock_path.name).is_file()
        assert not outside_lock.exists()
        linked = subprocess.run(
            [
                os.environ["COMSPEC"],
                "/d",
                "/c",
                "mklink",
                "/J",
                str(install_parent),
                str(outside),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert linked.returncode == 0, linked.stdout + linked.stderr

        holder.terminate()
        holder.wait(timeout=10)
        cleanup_script = created_cleanup_scripts[0]
        deadline = time.monotonic() + 90
        while cleanup_script.exists() and time.monotonic() < deadline:
            time.sleep(0.1)

        assert not cleanup_script.exists()
        assert (preserved_parent / "opensre.exe").read_bytes() == b"MZ-same-content"
        assert outside_target.read_bytes() == b"MZ-same-content"
        assert sentinel.read_text(encoding="utf-8") == "preserve"
        assert not outside_lock.exists()
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)
        if install_parent.is_junction():
            install_parent.rmdir()
        for cleanup_script in created_cleanup_scripts:
            cleanup_script.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows retirement identity regression")
def test_cleanup_worker_restores_flat_replacement_moved_at_retirement_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "late-swap-opensre.exe"
    target.write_bytes(b"scheduled executable")
    preserved_target = tmp_path / "preserved-scheduled-opensre.exe"
    replacement = tmp_path / "unrelated-replacement.exe"
    replacement.write_bytes(b"unrelated replacement")
    install_lock = tmp_path / ".opensre-app.install.lock"
    data_dir = tmp_path / "preserved-flat-data"
    data_dir.mkdir()
    data_file = data_dir / "state.json"
    data_file.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_flat_target_swap_before_retirement(
            _inject_empty_process_enumerator(read_cleanup_script()),
            target=target,
            preserved_target=preserved_target,
            replacement=replacement,
        ),
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    real_popen = subprocess.Popen
    workers: list[subprocess.Popen[bytes]] = []
    cleanup_scripts: list[Path] = []

    def _capture_worker(args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        cleanup_index = args.index("-CleanupScriptPath")
        cleanup_scripts.append(Path(args[cleanup_index + 1]))
        if "-CleanupScriptPath" not in args:
            return real_popen(args, **kwargs)
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        worker = real_popen(args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.subprocess.Popen", _capture_worker)

    try:
        ok, error = schedule_windows_cleanup(
            [target],
            parent_pid=2_147_483_647,
            data_paths=[data_dir],
            install_lock_path=install_lock,
        )

        assert ok is True, error
        assert len(workers) == 1
        output, _ = workers[0].communicate(timeout=90)
        assert workers[0].returncode == 1, output.decode("utf-8", errors="replace")
        assert len(cleanup_scripts) == 1
        assert not cleanup_scripts[0].exists()
        assert preserved_target.read_bytes() == b"scheduled executable"
        assert target.read_bytes() == b"unrelated replacement"
        assert not replacement.exists()
        assert not list(tmp_path.glob(f"{target.name}.uninstall-*"))
        assert install_lock.is_file()
        assert data_file.read_text(encoding="utf-8") == "preserve"
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
            worker.wait(timeout=10)
        for cleanup_script in cleanup_scripts:
            cleanup_script.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows retirement identity regression")
def test_managed_cleanup_worker_restores_junction_moved_at_retirement_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "managed-late-swap"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "build-1"
    version_dir.mkdir(parents=True)
    executable = version_dir / "opensre.exe"
    executable.write_bytes(b"scheduled managed executable")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    preserved_app_root = tmp_path / "preserved-managed-late-swap"
    outside_root = tmp_path / "unrelated-managed-root"
    outside_root.mkdir()
    outside_sentinel = outside_root / "state.json"
    outside_sentinel.write_text("preserve", encoding="utf-8")
    install_lock = install_dir / ".opensre-app.install.lock"
    data_dir = tmp_path / "preserved-managed-data"
    data_dir.mkdir()
    data_file = data_dir / "state.json"
    data_file.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_managed_app_swap_before_retirement(
            _inject_empty_process_enumerator(read_cleanup_script()),
            app_root=app_root,
            preserved_app_root=preserved_app_root,
            outside_root=outside_root,
        ),
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    real_popen = subprocess.Popen
    workers: list[subprocess.Popen[bytes]] = []
    cleanup_scripts: list[Path] = []

    def _capture_worker(args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        cleanup_index = args.index("-CleanupScriptPath")
        cleanup_scripts.append(Path(args[cleanup_index + 1]))
        if "-CleanupScriptPath" not in args:
            return real_popen(args, **kwargs)
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        worker = real_popen(args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.subprocess.Popen", _capture_worker)

    try:
        ok, error = schedule_windows_managed_cleanup(
            executable=executable,
            app_root=app_root,
            launcher=launcher,
            parent_pid=2_147_483_647,
            data_paths=[data_dir],
        )

        assert ok is True, error
        assert len(workers) == 1
        output, _ = workers[0].communicate(timeout=90)
        assert workers[0].returncode == 1, output.decode("utf-8", errors="replace")
        assert len(cleanup_scripts) == 1
        assert not cleanup_scripts[0].exists()
        assert app_root.is_junction()
        assert not list(install_dir.glob(".opensre-app.uninstall-*"))
        assert (preserved_app_root / "versions" / "build-1" / "opensre.exe").read_bytes() == (
            b"scheduled managed executable"
        )
        assert outside_sentinel.read_text(encoding="utf-8") == "preserve"
        assert launcher.is_file()
        assert install_lock.is_file()
        assert data_file.read_text(encoding="utf-8") == "preserve"
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
            worker.wait(timeout=10)
        if app_root.is_junction():
            app_root.rmdir()
        if preserved_app_root.exists() and not app_root.exists():
            preserved_app_root.rename(app_root)
        for cleanup_script in cleanup_scripts:
            cleanup_script.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows retirement identity regression")
def test_managed_cleanup_worker_restores_launcher_replaced_at_retirement_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "managed-launcher-late-swap"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "build-1"
    version_dir.mkdir(parents=True)
    executable = version_dir / "opensre.exe"
    executable.write_bytes(b"scheduled managed executable")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    preserved_launcher = tmp_path / "preserved-managed-launcher.cmd"
    replacement = tmp_path / "unrelated-launcher.cmd"
    replacement.write_text("@echo off\necho unrelated\n", encoding="utf-8")
    install_lock = install_dir / ".opensre-app.install.lock"
    data_dir = tmp_path / "preserved-launcher-data"
    data_dir.mkdir()
    data_file = data_dir / "state.json"
    data_file.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_flat_target_swap_before_retirement(
            _inject_empty_process_enumerator(read_cleanup_script()),
            target=launcher,
            preserved_target=preserved_launcher,
            replacement=replacement,
        ),
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    workers = _capture_cleanup_workers(monkeypatch)

    ok, error = schedule_windows_managed_cleanup(
        executable=executable,
        app_root=app_root,
        launcher=launcher,
        parent_pid=2_147_483_647,
        data_paths=[data_dir],
    )

    assert ok is True, error
    assert len(workers) == 1
    output, _ = workers[0].communicate(timeout=90)
    assert workers[0].returncode == 1, output.decode("utf-8", errors="replace")
    assert preserved_launcher.read_text(encoding="utf-8").endswith(
        ":: OpenSRE Windows launcher v1\n"
    )
    assert launcher.read_text(encoding="utf-8") == "@echo off\necho unrelated\n"
    assert not replacement.exists()
    assert not list(install_dir.glob("opensre.cmd.uninstall-*"))
    assert executable.read_bytes() == b"scheduled managed executable"
    assert install_lock.is_file()
    assert data_file.read_text(encoding="utf-8") == "preserve"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows retirement identity regression")
@pytest.mark.parametrize("directory", [False, True], ids=("file", "directory"))
def test_cleanup_worker_refuses_replacement_at_verified_retired_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    directory: bool,
) -> None:
    target = tmp_path / ("verified-directory" if directory else "verified-file.exe")
    replacement = tmp_path / ("replacement-directory" if directory else "replacement-file.exe")
    preserved_target = tmp_path / (
        "preserved-retired-directory" if directory else "preserved-retired-file.exe"
    )
    if directory:
        target.mkdir()
        (target / "scheduled.txt").write_text("scheduled", encoding="utf-8")
        replacement.mkdir()
        (replacement / "unrelated.txt").write_text("unrelated", encoding="utf-8")
    else:
        target.write_bytes(b"scheduled")
        replacement.write_bytes(b"unrelated")
    install_lock = tmp_path / ".opensre-app.install.lock"
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_retired_target_swap_before_removal(
            _inject_empty_process_enumerator(read_cleanup_script()),
            preserved_target=preserved_target,
            replacement=replacement,
            directory=directory,
        ),
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    workers = _capture_cleanup_workers(monkeypatch)

    ok, error = schedule_windows_cleanup(
        [target],
        parent_pid=2_147_483_647,
        install_lock_path=install_lock,
    )

    assert ok is True, error
    output, _ = workers[0].communicate(timeout=90)
    assert workers[0].returncode == 1, output.decode("utf-8", errors="replace")
    assert not target.exists()
    quarantines = list(tmp_path.glob(f"{target.name}.uninstall-*"))
    assert len(quarantines) == 1
    if directory:
        assert (preserved_target / "scheduled.txt").read_text(encoding="utf-8") == "scheduled"
        assert (quarantines[0] / "unrelated.txt").read_text(encoding="utf-8") == "unrelated"
    else:
        assert preserved_target.read_bytes() == b"scheduled"
        assert quarantines[0].read_bytes() == b"unrelated"
    assert not replacement.exists()
    assert install_lock.is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction regression")
def test_cleanup_worker_refuses_child_junction_swapped_before_recursive_deletion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "bundle-with-late-child-swap"
    child = target / "payload"
    child.mkdir(parents=True)
    (child / "scheduled.txt").write_text("scheduled", encoding="utf-8")
    preserved_child = tmp_path / "preserved-scheduled-child"
    outside_root = tmp_path / "outside-child-junction"
    outside_root.mkdir()
    outside_sentinel = outside_root / "unrelated.txt"
    outside_sentinel.write_text("preserve", encoding="utf-8")
    install_lock = tmp_path / ".opensre-app.install.lock"
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_child_junction_before_recursive_deletion(
            _inject_empty_process_enumerator(read_cleanup_script()),
            child_name=child.name,
            preserved_child=preserved_child,
            outside_root=outside_root,
        ),
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    workers = _capture_cleanup_workers(monkeypatch)
    retired_child: Path | None = None

    try:
        ok, error = schedule_windows_cleanup(
            [target],
            parent_pid=2_147_483_647,
            install_lock_path=install_lock,
        )

        assert ok is True, error
        output, _ = workers[0].communicate(timeout=90)
        assert workers[0].returncode == 1, output.decode("utf-8", errors="replace")
        quarantines = list(tmp_path.glob(f"{target.name}.uninstall-*"))
        assert len(quarantines) == 1
        retired_child = quarantines[0] / child.name
        assert retired_child.is_junction()
        assert (preserved_child / "scheduled.txt").read_text(encoding="utf-8") == "scheduled"
        assert outside_sentinel.read_text(encoding="utf-8") == "preserve"
        assert install_lock.is_file()
    finally:
        if retired_child is not None and retired_child.is_junction():
            retired_child.rmdir()
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
            worker.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows retirement identity regression")
def test_cleanup_worker_restores_data_directory_replaced_at_retirement_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_target = tmp_path / "scheduled-data"
    data_target.mkdir()
    (data_target / "scheduled.txt").write_text("scheduled", encoding="utf-8")
    preserved_target = tmp_path / "preserved-scheduled-data"
    replacement = tmp_path / "unrelated-data"
    replacement.mkdir()
    (replacement / "unrelated.txt").write_text("unrelated", encoding="utf-8")
    install_lock = tmp_path / ".opensre-app.install.lock"
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_directory_target_swap_before_retirement(
            _inject_empty_process_enumerator(read_cleanup_script()),
            target=data_target,
            preserved_target=preserved_target,
            replacement=replacement,
        ),
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    workers = _capture_cleanup_workers(monkeypatch)

    ok, error = schedule_windows_cleanup(
        [],
        parent_pid=2_147_483_647,
        data_paths=[data_target],
        install_lock_path=install_lock,
    )

    assert ok is True, error
    output, _ = workers[0].communicate(timeout=90)
    assert workers[0].returncode == 1, output.decode("utf-8", errors="replace")
    assert (preserved_target / "scheduled.txt").read_text(encoding="utf-8") == "scheduled"
    assert (data_target / "unrelated.txt").read_text(encoding="utf-8") == "unrelated"
    assert not replacement.exists()
    assert not list(tmp_path.glob(f"{data_target.name}.uninstall-*"))
    assert install_lock.is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows data identity regression")
def test_cleanup_worker_preserves_data_target_created_after_missing_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_target = tmp_path / "initially-missing-data"
    install_lock = tmp_path / ".opensre-app.install.lock"
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_missing_data_target_before_decision(
            _inject_empty_process_enumerator(read_cleanup_script()),
            target=data_target,
        ),
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    workers = _capture_cleanup_workers(monkeypatch)

    ok, error = schedule_windows_cleanup(
        [],
        parent_pid=2_147_483_647,
        data_paths=[data_target],
        install_lock_path=install_lock,
    )

    assert ok is True, error
    output, _ = workers[0].communicate(timeout=90)
    assert workers[0].returncode == 1, output.decode("utf-8", errors="replace")
    assert (data_target / "unrelated.txt").read_text(encoding="utf-8") == "preserve"
    assert install_lock.is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows data transaction regression")
def test_cleanup_worker_second_data_preparation_failure_restores_all_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_targets = [tmp_path / "first-data", tmp_path / "second-data"]
    for index, data_target in enumerate(data_targets):
        data_target.mkdir()
        (data_target / "state.json").write_text(f"preserve-{index}", encoding="utf-8")
    install_lock = tmp_path / ".opensre-app.install.lock"
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_deletion_preparation_failure(
            _inject_empty_process_enumerator(read_cleanup_script())
        ),
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    workers = _capture_cleanup_workers(monkeypatch)

    ok, error = schedule_windows_cleanup(
        [],
        parent_pid=2_147_483_647,
        data_paths=data_targets,
        install_lock_path=install_lock,
    )

    assert ok is True, error
    output, _ = workers[0].communicate(timeout=90)
    assert workers[0].returncode == 1, output.decode("utf-8", errors="replace")
    for index, data_target in enumerate(data_targets):
        assert (data_target / "state.json").read_text(encoding="utf-8") == (f"preserve-{index}")
        assert list(tmp_path.glob(f"{data_target.name}.uninstall-*")) == []
    assert install_lock.is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
@pytest.mark.parametrize("managed", [False, True], ids=("legacy", "managed"))
def test_cleanup_worker_holds_executable_guard_through_retirement_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    managed: bool,
) -> None:
    from tests.cli.test_install_ps1_onedir import _fake_opensre_executable

    fake_opensre = _fake_opensre_executable()
    install_dir = tmp_path / ("managed guard" if managed else "legacy guard")
    install_dir.mkdir()
    install_lock = install_dir / ".opensre-app.install.lock"
    late_launch_marker = tmp_path / f"late-launch-{'managed' if managed else 'legacy'}"
    launcher: Path | None = None
    app_root: Path | None = None
    if managed:
        app_root = install_dir / ".opensre-app"
        target = app_root / "versions" / "build-1"
        target.mkdir(parents=True)
        executable = target / "opensre.exe"
        shutil.copy2(fake_opensre, executable)
        (app_root / "layout-v1.marker").write_text(
            "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
        )
        (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
        launcher = install_dir / "opensre.cmd"
        launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    else:
        target = install_dir / "legacy-bundle"
        target.mkdir()
        executable = target / "opensre.exe"
        shutil.copy2(fake_opensre, executable)
    baseline = subprocess.run(
        [str(executable), "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_late_launch_probe(
            read_cleanup_script(),
            marker=late_launch_marker,
            managed=managed,
        ),
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    real_popen = subprocess.Popen
    workers: list[subprocess.Popen[bytes]] = []

    def _capture_worker(args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        if "-CleanupScriptPath" not in args:
            return real_popen(args, **kwargs)
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        worker = real_popen(args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.subprocess.Popen", _capture_worker)

    try:
        if managed:
            assert app_root is not None
            ok, error = schedule_windows_managed_cleanup(
                executable=executable,
                app_root=app_root,
                launcher=launcher,
                parent_pid=holder.pid,
            )
        else:
            ok, error = schedule_windows_cleanup(
                [target],
                parent_pid=holder.pid,
                install_lock_path=install_lock,
            )
        assert ok is True, error
        assert len(workers) == 1
        holder.terminate()
        holder.wait(timeout=10)
        output, _ = workers[0].communicate(timeout=90)
        assert workers[0].returncode == 0, output.decode("utf-8", errors="replace")
        assert late_launch_marker.read_text(encoding="utf-8") == "blocked"
        assert not install_lock.exists()
        assert not target.exists()
        if app_root is not None:
            assert not app_root.exists()
        if launcher is not None:
            assert not launcher.exists()
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
            worker.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows retirement race")
@pytest.mark.parametrize("worker_kind", ["installer", "legacy", "managed"])
def test_cleanup_worker_preserves_bundle_when_process_launches_before_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, worker_kind: str
) -> None:
    from tests.cli.test_install_ps1_onedir import (
        INSTALL_PS1,
        _fake_opensre_executable,
        _schedule_deferred_cleanup,
        _wait_until,
    )

    managed = worker_kind == "managed"

    install_dir = tmp_path / "launch race"
    app_root = install_dir / ".opensre-app"
    version = app_root / "versions" / "build-1"
    version.mkdir(parents=True)
    executable = version / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), executable)
    (app_root / "layout-v1.marker").write_text("OpenSRE Windows bundle layout v1\n")
    (app_root / "current.txt").write_text(
        "active-build\n" if worker_kind == "installer" else "build-1\n"
    )
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n")
    data = tmp_path / "user data"
    data.mkdir()
    sentinel = data / "state.json"
    sentinel.write_text("preserve")
    ready = tmp_path / "retired-executable.txt"
    proceed = tmp_path / "allow-guard"
    release = tmp_path / "release-process"

    def _worker_source() -> str:
        source = (
            INSTALL_PS1.read_text(encoding="utf-8")
            if worker_kind == "installer"
            else read_cleanup_script()
        )
        if worker_kind == "installer":
            anchor = "        try {\n            # An open child handle prevents Windows from renaming its directory.\n"
            image_path = "Join-Path $retiredPath 'opensre.exe'"
        elif managed:
            anchor = "            $movedAppRootMatches = $true\n"
            image_path = 'Join-Path $movedAppRoot "versions\\$expectedInstallId\\opensre.exe"'
        else:
            anchor = "        $retiredIdentityMatches = $true\n"
            image_path = "Join-Path $retiredPath 'opensre.exe'"
        assert source.count(anchor) == 1
        barrier = f"""
        [System.IO.File]::WriteAllText('{ready}', ({image_path}))
        $testDeadline = [System.DateTime]::UtcNow.AddSeconds(90)
        while (-not [System.IO.File]::Exists('{proceed}')) {{
            if ([System.DateTime]::UtcNow -ge $testDeadline) {{ throw 'Launch barrier timed out' }}
            Start-Sleep -Milliseconds 25
        }}
"""
        return source.replace(anchor, barrier + anchor, 1)

    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script", _worker_source
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity", _missing_process_identity
    )
    workers = _capture_cleanup_workers(monkeypatch)
    child: subprocess.Popen[bytes] | None = None
    try:
        if worker_kind == "installer":
            command_path = tmp_path / "worker-command.txt"
            source = _worker_source()
            launch = "Start-Process -FilePath $powershellPath -ArgumentList $arguments -WindowStyle Hidden | Out-Null"
            assert source.count(launch) == 1
            source = source.replace(
                launch,
                f"[System.IO.File]::WriteAllText('{command_path}', "
                "('\"' + $powershellPath + '\" ' + ($arguments -join ' ')))",
                1,
            )
            installer = tmp_path / "installer.ps1"
            installer.write_text(source, encoding="utf-8")
            scheduled, cleanup_path = _schedule_deferred_cleanup(
                installer_path=installer,
                layout_root=app_root,
                target=version,
                cwd=tmp_path,
            )
            assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
            assert cleanup_path is not None
            workers.append(
                subprocess.Popen(
                    command_path.read_text(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
            )
            ok, error = True, None
        elif managed:
            ok, error = schedule_windows_managed_cleanup(
                executable=executable,
                app_root=app_root,
                launcher=launcher,
                parent_pid=2_147_483_647,
                data_paths=[data],
            )
        else:
            ok, error = schedule_windows_cleanup(
                [version],
                parent_pid=2_147_483_647,
                data_paths=[data],
                install_lock_path=install_dir / ".opensre-app.install.lock",
            )
        assert ok, error
        assert len(workers) == 1
        _wait_until(lambda: ready.exists() or workers[0].poll() is not None)
        assert ready.exists()
        child_ready = tmp_path / "child-running"
        child = subprocess.Popen([ready.read_text(), "hold-until", str(release), str(child_ready)])
        _wait_until(lambda: child_ready.exists() or child.poll() is not None)
        assert child_ready.exists()
        assert child.poll() is None
        proceed.write_text("continue")
        output, _ = workers[0].communicate(timeout=90)
        assert workers[0].returncode == (0 if worker_kind == "installer" else 1), output.decode(
            errors="replace"
        )
        assert child.poll() is None
        assert executable.is_file()
        assert sentinel.read_text() == "preserve"
        assert launcher.is_file()
        assert not list(install_dir.rglob("*.uninstall-*"))
        assert not list(app_root.glob("retired-*"))
    finally:
        proceed.write_text("continue")
        release.write_text("release")
        if child is not None:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=10)
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
            worker.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
@pytest.mark.parametrize("managed", [False, True], ids=("legacy", "managed"))
def test_cleanup_worker_holds_executable_guard_through_recursive_deletion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    managed: bool,
) -> None:
    from tests.cli.test_install_ps1_onedir import _fake_opensre_executable

    fake_opensre = _fake_opensre_executable()
    install_dir = tmp_path / ("managed deletion guard" if managed else "legacy deletion guard")
    install_dir.mkdir()
    install_lock = install_dir / ".opensre-app.install.lock"
    launch_marker = tmp_path / f"deletion-launch-{'managed' if managed else 'legacy'}"
    launcher: Path | None = None
    app_root: Path | None = None
    if managed:
        app_root = install_dir / ".opensre-app"
        target = app_root / "versions" / "build-1"
        target.mkdir(parents=True)
        executable = target / "opensre.exe"
        executable_relative_path = Path("versions") / "build-1" / "opensre.exe"
        shutil.copy2(fake_opensre, executable)
        (app_root / "layout-v1.marker").write_text(
            "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
        )
        (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
        launcher = install_dir / "opensre.cmd"
        launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    else:
        target = install_dir / "legacy-bundle"
        target.mkdir()
        executable = target / "opensre.exe"
        executable_relative_path = Path("opensre.exe")
        shutil.copy2(fake_opensre, executable)
    baseline = subprocess.run(
        [str(executable), "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr

    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_launch_probe_before_recursive_deletion(
            _inject_empty_process_enumerator(read_cleanup_script()),
            marker=launch_marker,
            executable_relative_path=executable_relative_path,
        ),
    )
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    workers = _capture_cleanup_workers(monkeypatch)

    if managed:
        assert app_root is not None
        ok, error = schedule_windows_managed_cleanup(
            executable=executable,
            app_root=app_root,
            launcher=launcher,
            parent_pid=2_147_483_647,
        )
    else:
        ok, error = schedule_windows_cleanup(
            [target],
            parent_pid=2_147_483_647,
            install_lock_path=install_lock,
        )

    assert ok is True, error
    output, _ = workers[0].communicate(timeout=90)
    assert workers[0].returncode == 0, output.decode("utf-8", errors="replace")
    assert launch_marker.read_text(encoding="utf-8") == "blocked"
    assert not install_lock.exists()
    if app_root is not None:
        assert not app_root.exists()
    else:
        assert not target.exists()
    if launcher is not None:
        assert not launcher.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction regression")
def test_cleanup_worker_treats_dangling_junction_as_an_existing_guard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "late dangling guard"
    junction_destination = tmp_path / "removed junction destination"
    junction_destination.mkdir()
    data_dir = tmp_path / "preserved dangling guard data"
    data_dir.mkdir()
    data_file = data_dir / "state.json"
    data_file.write_text("preserve", encoding="utf-8")
    install_lock = tmp_path / "dangling-guard-cleanup.lock"
    created_cleanup_scripts: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def _mkstemp(*, prefix: str, suffix: str) -> tuple[int, str]:
        descriptor, name = real_mkstemp(prefix=prefix, suffix=suffix, dir=tmp_path)
        created_cleanup_scripts.append(Path(name))
        return descriptor, name

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.tempfile.mkstemp", _mkstemp)
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )

    try:
        ok, error = schedule_windows_cleanup(
            [target],
            parent_pid=holder.pid,
            data_paths=[data_dir],
            install_lock_path=install_lock,
            data_guard_paths=[target],
        )
        assert ok is True, error
        linked = subprocess.run(
            [
                os.environ["COMSPEC"],
                "/d",
                "/c",
                "mklink",
                "/J",
                str(target),
                str(junction_destination),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert linked.returncode == 0, linked.stdout + linked.stderr
        junction_destination.rmdir()
        assert target.is_junction()
        assert not target.exists()

        holder.terminate()
        holder.wait(timeout=10)
        cleanup_script = created_cleanup_scripts[0]
        deadline = time.monotonic() + 90
        while cleanup_script.exists() and time.monotonic() < deadline:
            time.sleep(0.1)

        assert not cleanup_script.exists()
        assert target.is_junction()
        assert data_file.read_text(encoding="utf-8") == "preserve"
        assert install_lock.is_file()
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)
        if target.is_junction():
            target.rmdir()
        for cleanup_script in created_cleanup_scripts:
            cleanup_script.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
@pytest.mark.parametrize("managed", [False, True], ids=("legacy", "managed"))
def test_cleanup_worker_opens_long_retired_executable_with_extended_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    managed: bool,
) -> None:
    from tests.cli.test_install_ps1_onedir import _fake_opensre_executable

    relative_executable = (
        Path(".opensre-app") / "versions" / "build-1" / "opensre.exe"
        if managed
        else Path("legacy-bundle") / "opensre.exe"
    )
    retirement_suffix_length = len(".uninstall-") + 32
    install_dir = tmp_path / ("managed long guard" if managed else "legacy long guard")
    while len(str(install_dir / relative_executable)) + retirement_suffix_length <= 260:
        install_dir /= "x"

    executable = install_dir / relative_executable
    executable.parent.mkdir(parents=True)
    shutil.copy2(_fake_opensre_executable(), executable)
    assert len(str(executable)) < 260
    assert len(str(executable)) + retirement_suffix_length > 260

    install_lock = install_dir / ".opensre-app.install.lock"
    launcher: Path | None = None
    app_root: Path | None = None
    target = executable.parent
    if managed:
        app_root = install_dir / ".opensre-app"
        target = app_root
        (app_root / "layout-v1.marker").write_text(
            "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
        )
        (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
        launcher = install_dir / "opensre.cmd"
        launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")

    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    workers = _capture_cleanup_workers(monkeypatch)

    try:
        if managed:
            assert app_root is not None
            ok, error = schedule_windows_managed_cleanup(
                executable=executable,
                app_root=app_root,
                launcher=launcher,
                parent_pid=2_147_483_647,
            )
        else:
            ok, error = schedule_windows_cleanup(
                [target],
                parent_pid=2_147_483_647,
                install_lock_path=install_lock,
            )

        assert ok is True, error
        assert len(workers) == 1
        output, _ = workers[0].communicate(timeout=90)
        assert workers[0].returncode == 0, output.decode("utf-8", errors="replace")
        assert not target.exists()
        assert not install_lock.exists()
        if launcher is not None:
            assert not launcher.exists()
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
            worker.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
def test_managed_uninstall_removes_long_quarantine_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_dir = tmp_path / "managed uninstall with spaces"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "old-build"
    payload_dir = version_dir / "_internal"
    while len(str(payload_dir / "payload.txt")) <= 225:
        payload_dir /= "nested-content-filter"
    payload_dir.mkdir(parents=True)
    payload = payload_dir / "payload.txt"
    payload.write_text("temporary", encoding="utf-8")
    assert len(str(payload)) < 260
    assert len(str(payload)) + len(".uninstall-") + 32 > 260

    executable = version_dir / "opensre.exe"
    executable.write_bytes(b"MZ")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("old-build\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    install_lock = install_dir / ".opensre-app.install.lock"
    install_lock.write_bytes(b"")
    unrelated = install_dir / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    data_dir = tmp_path / "managed uninstall data"
    data_dir.mkdir()
    (data_dir / "state.json").write_text("remove", encoding="utf-8")
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    cleanup_workers: list[subprocess.Popen[Any]] = []
    real_popen = subprocess.Popen
    worker_log = tmp_path / "cleanup-worker.log"

    with worker_log.open("wb") as log_stream:

        def _capture_cleanup_worker(args: list[str], **kwargs: Any) -> subprocess.Popen[Any]:
            is_cleanup = "-CleanupPayload" in args
            if is_cleanup:
                kwargs["stdout"] = log_stream
                kwargs["stderr"] = subprocess.STDOUT
            process = real_popen(args, **kwargs)
            if is_cleanup:
                cleanup_workers.append(process)
            return process

        try:
            with monkeypatch.context() as context:
                context.setattr(
                    "surfaces.cli.lifecycle.windows.cleanup.subprocess.Popen",
                    _capture_cleanup_worker,
                )
                ok, err = schedule_windows_managed_cleanup(
                    executable=executable,
                    app_root=app_root,
                    launcher=launcher,
                    parent_pid=holder.pid,
                    data_paths=[data_dir],
                )
            assert ok is True, err
            assert len(cleanup_workers) == 1
            worker = cleanup_workers[0]

            holder.terminate()
            holder.wait(timeout=10)
            # Filesystem state alone cannot distinguish worker success from an
            # early refusal or prove the detached child has finished.
            exit_code = worker.wait(timeout=90)
            assert exit_code == 0, worker_log.read_text(encoding="utf-8", errors="replace")

            assert not app_root.exists()
            assert not launcher.exists()
            assert not install_lock.exists()
            assert not data_dir.exists()
            assert list(install_dir.glob("*.uninstall-*")) == []
            assert unrelated.read_text(encoding="utf-8") == "keep"
        finally:
            if holder.poll() is None:
                holder.terminate()
                holder.wait(timeout=10)
            for worker in cleanup_workers:
                if worker.poll() is None:
                    worker.terminate()
                worker.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
@pytest.mark.parametrize("residual_name", ["opensre.exe", "opensre.cmd"])
def test_managed_uninstall_worker_preserves_data_for_residual_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    residual_name: str,
) -> None:
    install_dir = tmp_path / f"managed residual {residual_name}"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "old-build"
    version_dir.mkdir(parents=True)
    executable = version_dir / "opensre.exe"
    executable.write_bytes(b"MZ-managed")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("old-build\n", encoding="utf-8")
    install_lock = install_dir / ".opensre-app.install.lock"
    install_lock.write_bytes(b"")

    launcher = install_dir / "opensre.cmd"
    launcher_to_remove: Path | None = None
    if residual_name == "opensre.exe":
        launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
        launcher_to_remove = launcher
        residual = install_dir / residual_name
        residual.write_bytes(b"MZ-user-owned")
    else:
        residual = launcher
        residual.write_text("@echo off\necho user-owned\n", encoding="utf-8")

    residual_before = residual.read_bytes()
    data_dir = tmp_path / f"managed residual data {residual_name}"
    data_dir.mkdir()
    data_file = data_dir / "state.json"
    data_file.write_text("preserve while an entrypoint remains", encoding="utf-8")
    unrelated = install_dir / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    created_cleanup_scripts: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def _mkstemp(*, prefix: str, suffix: str) -> tuple[int, str]:
        descriptor, name = real_mkstemp(prefix=prefix, suffix=suffix, dir=tmp_path)
        created_cleanup_scripts.append(Path(name))
        return descriptor, name

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.tempfile.mkstemp", _mkstemp)
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )

    try:
        ok, error = schedule_windows_managed_cleanup(
            executable=executable,
            app_root=app_root,
            launcher=launcher_to_remove,
            parent_pid=holder.pid,
            data_paths=[data_dir],
        )
        assert ok is True
        assert error is None
        assert len(created_cleanup_scripts) == 1

        holder.terminate()
        holder.wait(timeout=10)
        cleanup_script = created_cleanup_scripts[0]
        deadline = time.monotonic() + 90
        while cleanup_script.exists() and time.monotonic() < deadline:
            time.sleep(0.1)

        assert not cleanup_script.exists()
        assert not app_root.exists()
        assert not install_lock.exists()
        assert list(install_dir.glob("*.uninstall-*")) == []
        assert residual.read_bytes() == residual_before
        assert data_file.read_text(encoding="utf-8") == ("preserve while an entrypoint remains")
        assert unrelated.read_text(encoding="utf-8") == "keep"
        if launcher_to_remove is not None:
            assert not launcher_to_remove.exists()
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)
        for cleanup_script in created_cleanup_scripts:
            cleanup_script.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
def test_managed_uninstall_worker_preserves_a_reinstalled_bundle(tmp_path: Path) -> None:
    install_dir = tmp_path / "reinstall race"
    app_root = install_dir / ".opensre-app"
    old_version = app_root / "versions" / "old-build"
    new_version = app_root / "versions" / "new-build"
    old_version.mkdir(parents=True)
    new_version.mkdir(parents=True)
    executable = old_version / "opensre.exe"
    executable.write_bytes(b"MZ")
    (new_version / "opensre.exe").write_bytes(b"MZ-new")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    pointer = app_root / "current.txt"
    pointer.write_text("old-build\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    install_lock = install_dir / ".opensre-app.install.lock"
    install_lock.write_bytes(b"")
    data_dir = tmp_path / "reinstalled user data"
    data_dir.mkdir()
    data_file = data_dir / "state.json"
    data_file.write_text("keep for new install", encoding="utf-8")
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )

    try:
        ok, err = schedule_windows_managed_cleanup(
            executable=executable,
            app_root=app_root,
            launcher=launcher,
            parent_pid=holder.pid,
            data_paths=[data_dir],
        )
        assert ok is True
        assert err is None

        pointer.write_text("new-build\n", encoding="utf-8")
        holder.terminate()
        holder.wait(timeout=10)
        deadline = time.monotonic() + 90
        while old_version.exists() and time.monotonic() < deadline:
            time.sleep(0.1)

        assert not old_version.exists()
        assert new_version.is_dir()
        assert app_root.is_dir()
        assert launcher.is_file()
        assert install_lock.is_file()
        assert pointer.read_text(encoding="utf-8").strip() == "new-build"
        assert data_file.read_text(encoding="utf-8") == "keep for new install"
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
@pytest.mark.parametrize(
    "invalid_pointer",
    ("..\n", "old-build\nother-build\n"),
    ids=("dotdot", "multiple-lines"),
)
def test_managed_uninstall_worker_rejects_malformed_pointer_race(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid_pointer: str,
) -> None:
    install_dir = tmp_path / "dotdot pointer worker race"
    app_root = install_dir / ".opensre-app"
    old_version = app_root / "versions" / "old-build"
    old_version.mkdir(parents=True)
    executable = old_version / "opensre.exe"
    executable.write_bytes(b"MZ-old")
    alias_executable = app_root / "opensre.exe"
    alias_executable.write_bytes(b"MZ-invalid-pointer-target")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    pointer = app_root / "current.txt"
    pointer.write_text("old-build\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    install_lock = install_dir / ".opensre-app.install.lock"
    install_lock.write_bytes(b"")
    data_dir = tmp_path / "dotdot pointer user data"
    data_dir.mkdir()
    data_file = data_dir / "state.json"
    data_file.write_text("keep", encoding="utf-8")
    cleanup_scripts: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def _mkstemp(*, prefix: str, suffix: str) -> tuple[int, str]:
        descriptor, name = real_mkstemp(prefix=prefix, suffix=suffix, dir=tmp_path)
        cleanup_scripts.append(Path(name))
        return descriptor, name

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.tempfile.mkstemp", _mkstemp)
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )

    try:
        ok, err = schedule_windows_managed_cleanup(
            executable=executable,
            app_root=app_root,
            launcher=launcher,
            parent_pid=holder.pid,
            data_paths=[data_dir],
        )
        assert ok is True
        assert err is None
        assert len(cleanup_scripts) == 1

        pointer.write_text(invalid_pointer, encoding="utf-8")
        holder.terminate()
        holder.wait(timeout=10)
        deadline = time.monotonic() + 90
        while cleanup_scripts[0].exists() and time.monotonic() < deadline:
            time.sleep(0.1)

        assert not cleanup_scripts[0].exists()
        assert old_version.is_dir()
        assert executable.read_bytes() == b"MZ-old"
        assert alias_executable.read_bytes() == b"MZ-invalid-pointer-target"
        assert pointer.read_text(encoding="utf-8") == invalid_pointer
        assert launcher.is_file()
        assert install_lock.is_file()
        assert data_file.read_text(encoding="utf-8") == "keep"
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)
        for cleanup_script in cleanup_scripts:
            cleanup_script.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
def test_legacy_uninstall_worker_preserves_data_when_onedir_install_wins_race(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "legacy reinstall race"
    install_dir.mkdir()
    executable = install_dir / "opensre.exe"
    executable.write_bytes(b"MZ-old")
    install_lock = install_dir / ".opensre-app.install.lock"
    app_root = install_dir / ".opensre-app"
    launcher = install_dir / "opensre.cmd"
    data_dir = tmp_path / "legacy reinstall data"
    data_dir.mkdir()
    data_file = data_dir / "state.json"
    data_file.write_text("keep for new install", encoding="utf-8")
    unrelated = install_dir / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    created_cleanup_scripts: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def _mkstemp(*, prefix: str, suffix: str) -> tuple[int, str]:
        descriptor, name = real_mkstemp(prefix=prefix, suffix=suffix, dir=tmp_path)
        created_cleanup_scripts.append(Path(name))
        return descriptor, name

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.tempfile.mkstemp", _mkstemp)
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )

    try:
        ok, error = schedule_windows_cleanup(
            [executable],
            parent_pid=holder.pid,
            data_paths=[data_dir],
            install_lock_path=install_lock,
            data_guard_paths=[app_root, launcher, executable],
        )
        assert ok is True
        assert error is None
        assert len(created_cleanup_scripts) == 1

        new_executable = app_root / "versions" / "new-build" / "opensre.exe"
        new_executable.parent.mkdir(parents=True)
        new_executable.write_bytes(b"MZ-new")
        (app_root / "layout-v1.marker").write_text(
            "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
        )
        (app_root / "current.txt").write_text("new-build\n", encoding="utf-8")
        launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")

        holder.terminate()
        holder.wait(timeout=10)
        cleanup_script = created_cleanup_scripts[0]
        deadline = time.monotonic() + 90
        while cleanup_script.exists() and time.monotonic() < deadline:
            time.sleep(0.1)

        assert not cleanup_script.exists()
        assert not executable.exists()
        assert new_executable.read_bytes() == b"MZ-new"
        assert launcher.is_file()
        assert install_lock.is_file()
        assert data_file.read_text(encoding="utf-8") == "keep for new install"
        assert unrelated.read_text(encoding="utf-8") == "keep"
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
def test_legacy_uninstall_worker_preserves_data_when_flat_reinstall_wins_race(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "flat legacy reinstall race"
    install_dir.mkdir()
    executable = install_dir / "opensre.exe"
    executable.write_bytes(b"MZ-old")
    install_lock = install_dir / ".opensre-app.install.lock"
    data_dir = tmp_path / "flat reinstall data"
    data_dir.mkdir()
    data_file = data_dir / "state.json"
    data_file.write_text("keep for replacement", encoding="utf-8")
    unrelated = install_dir / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    ready = tmp_path / "flat-data-guard-ready"
    release = tmp_path / "flat-data-guard-release"
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_empty_process_enumerator(
            _inject_before_data_guard_barrier(
                read_cleanup_script(),
                ready=ready,
                release=release,
            )
        ),
    )
    created_cleanup_scripts: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def _mkstemp(*, prefix: str, suffix: str) -> tuple[int, str]:
        descriptor, name = real_mkstemp(prefix=prefix, suffix=suffix, dir=tmp_path)
        created_cleanup_scripts.append(Path(name))
        return descriptor, name

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.tempfile.mkstemp", _mkstemp)
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )

    try:
        ok, error = schedule_windows_cleanup(
            [executable],
            parent_pid=2_147_483_647,
            data_paths=[data_dir],
            install_lock_path=install_lock,
            data_guard_paths=[
                install_dir / ".opensre-app",
                install_dir / "opensre.cmd",
                executable,
            ],
        )
        assert ok is True, error
        assert len(created_cleanup_scripts) == 1

        deadline = time.monotonic() + 90
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert ready.is_file()
        assert not executable.exists()

        executable.write_bytes(b"MZ-new-flat")
        release.write_text("continue", encoding="utf-8")
        cleanup_script = created_cleanup_scripts[0]
        deadline = time.monotonic() + 90
        while cleanup_script.exists() and time.monotonic() < deadline:
            time.sleep(0.1)

        assert not cleanup_script.exists()
        assert executable.read_bytes() == b"MZ-new-flat"
        assert install_lock.is_file()
        assert data_file.read_text(encoding="utf-8") == "keep for replacement"
        assert unrelated.read_text(encoding="utf-8") == "keep"
    finally:
        release.write_text("continue", encoding="utf-8")
        for cleanup_script in created_cleanup_scripts:
            cleanup_script.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
def test_legacy_uninstall_worker_preserves_same_content_flat_reinstall(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "same content flat reinstall race"
    install_dir.mkdir()
    executable = install_dir / "opensre.exe"
    executable.write_bytes(b"MZ-same-content")
    original_metadata = executable.stat(follow_symlinks=False)
    original_identity = (original_metadata.st_ino, original_metadata.st_ctime_ns)
    install_lock = install_dir / ".opensre-app.install.lock"
    data_dir = tmp_path / "same content reinstall data"
    data_dir.mkdir()
    data_file = data_dir / "state.json"
    data_file.write_text("keep for replacement", encoding="utf-8")
    unrelated = install_dir / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    created_cleanup_scripts: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def _mkstemp(*, prefix: str, suffix: str) -> tuple[int, str]:
        descriptor, name = real_mkstemp(prefix=prefix, suffix=suffix, dir=tmp_path)
        created_cleanup_scripts.append(Path(name))
        return descriptor, name

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.tempfile.mkstemp", _mkstemp)
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )

    try:
        ok, error = schedule_windows_cleanup(
            [executable],
            parent_pid=holder.pid,
            data_paths=[data_dir],
            install_lock_path=install_lock,
            data_guard_paths=[executable],
        )
        assert ok is True, error

        executable.unlink()
        executable.write_bytes(b"MZ-same-content")
        replacement_metadata = executable.stat(follow_symlinks=False)
        replacement_identity = (
            replacement_metadata.st_ino,
            replacement_metadata.st_ctime_ns,
        )
        assert replacement_identity != original_identity

        holder.terminate()
        holder.wait(timeout=10)
        cleanup_script = created_cleanup_scripts[0]
        deadline = time.monotonic() + 90
        while cleanup_script.exists() and time.monotonic() < deadline:
            time.sleep(0.1)

        assert not cleanup_script.exists()
        assert executable.read_bytes() == b"MZ-same-content"
        assert install_lock.is_file()
        assert data_file.read_text(encoding="utf-8") == "keep for replacement"
        assert unrelated.read_text(encoding="utf-8") == "keep"
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)
        for cleanup_script in created_cleanup_scripts:
            cleanup_script.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
def test_managed_uninstall_holds_install_lock_through_data_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "uninstall lock transaction"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "build-1"
    (version_dir / "_internal").mkdir(parents=True)
    executable = version_dir / "opensre.exe"
    executable.write_bytes(b"MZ")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    install_lock = install_dir / ".opensre-app.install.lock"
    install_lock.write_bytes(b"")
    data_dir = tmp_path / "locked transaction data"
    data_dir.mkdir()
    (data_dir / "state.json").write_text("remove", encoding="utf-8")
    unrelated = install_dir / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    ready = tmp_path / "data-decision-ready"
    release = tmp_path / "data-decision-release"
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_empty_process_enumerator(
            _inject_data_decision_barrier(
                read_cleanup_script(),
                ready=ready,
                release=release,
            )
        ),
    )
    created_cleanup_scripts: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def _mkstemp(*, prefix: str, suffix: str) -> tuple[int, str]:
        descriptor, name = real_mkstemp(prefix=prefix, suffix=suffix, dir=tmp_path)
        created_cleanup_scripts.append(Path(name))
        return descriptor, name

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.tempfile.mkstemp", _mkstemp)
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )

    ok, error = schedule_windows_managed_cleanup(
        executable=executable,
        app_root=app_root,
        launcher=launcher,
        parent_pid=2_147_483_647,
        data_paths=[data_dir],
    )
    assert ok is True
    assert error is None
    deadline = time.monotonic() + 90
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.1)

    assert ready.is_file()
    try:
        with pytest.raises(OSError), install_lock.open("r+b"):
            pass
    finally:
        release.write_text("continue", encoding="utf-8")

    cleanup_script = created_cleanup_scripts[0]
    deadline = time.monotonic() + 90
    while cleanup_script.exists() and time.monotonic() < deadline:
        time.sleep(0.1)

    assert not cleanup_script.exists()
    assert not data_dir.exists()
    assert not app_root.exists()
    assert not launcher.exists()
    assert not install_lock.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
@pytest.mark.parametrize("scan_failure", ["transient", "persistent"])
def test_managed_uninstall_worker_retains_tree_when_process_scan_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scan_failure: str,
) -> None:
    install_dir = tmp_path / "uninstall process scan failure"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "build-1"
    payload = version_dir / "_internal" / "lazy" / "payload.dat"
    payload.parent.mkdir(parents=True)
    payload.write_text("must remain complete", encoding="utf-8")
    executable = version_dir / "opensre.exe"
    executable.write_bytes(b"MZ")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    launcher_before = launcher.read_bytes()
    install_lock = install_dir / ".opensre-app.install.lock"
    install_lock.write_bytes(b"")
    unrelated = install_dir / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    data_dir = tmp_path / "process scan failure data"
    data_dir.mkdir()
    data_file = data_dir / "state.json"
    data_file.write_text("keep", encoding="utf-8")
    before = {
        path.relative_to(app_root): path.read_bytes()
        for path in app_root.rglob("*")
        if path.is_file()
    }

    def _worker_source() -> str:
        source = read_cleanup_script()
        anchor = "$ErrorActionPreference = 'Stop'\n"
        assert source.count(anchor) == 1
        scanner = r"""
$script:scanCount = 0
function Get-Process {
    [CmdletBinding()]
    param([int]$Id, [string]$Name)
    if ($PSBoundParameters.ContainsKey('Id')) {
        return Microsoft.PowerShell.Management\Get-Process @PSBoundParameters
    }
    $script:scanCount++
    [Console]::WriteLine("SCAN=$script:scanCount")
    if ($script:scanCount -eq 1 -or '__FAILURE__' -eq 'persistent') {
        $vanished = [pscustomobject]@{ ProcessName = 'opensre'; HasExited = $false }
        $vanished | Add-Member -MemberType ScriptProperty -Name Path -Value {
            [Console]::WriteLine('PROCESS_DISAPPEARED_DURING_INSPECTION')
            throw 'process exited after enumeration'
        }
        return $vanished
    }
    return @()
}
function Start-Sleep {
    param([int]$Milliseconds)
    [Console]::WriteLine('RETRY')
    if ($script:scanCount -ge 2) {
        # Exhaust the shared deadline deterministically, without wall-clock sleeps.
        $script:lockDeadline = [System.DateTime]::MinValue
    }
}
"""
        return source.replace(anchor, anchor + scanner.replace("__FAILURE__", scan_failure), 1)

    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script", _worker_source
    )
    real_popen = subprocess.Popen
    workers: list[subprocess.Popen[bytes]] = []

    def _run_worker(args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.STDOUT
        worker = real_popen(args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.subprocess.Popen", _run_worker)
    created_cleanup_scripts: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def _mkstemp(*, prefix: str, suffix: str) -> tuple[int, str]:
        descriptor, name = real_mkstemp(prefix=prefix, suffix=suffix, dir=tmp_path)
        created_cleanup_scripts.append(Path(name))
        return descriptor, name

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.tempfile.mkstemp", _mkstemp)
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )

    ok, error = schedule_windows_managed_cleanup(
        executable=executable,
        app_root=app_root,
        launcher=launcher,
        parent_pid=2_147_483_647,
        data_paths=[data_dir],
    )
    assert ok is True
    assert error is None
    assert len(created_cleanup_scripts) == 1
    cleanup_script = created_cleanup_scripts[0]
    assert len(workers) == 1
    output, _ = workers[0].communicate(timeout=90)
    diagnostics = output.decode("utf-8", errors="replace")
    exit_code = workers[0].returncode
    assert not cleanup_script.exists()
    assert "PROCESS_DISAPPEARED_DURING_INSPECTION" in diagnostics
    assert exit_code == (0 if scan_failure == "transient" else 1), diagnostics
    assert "SCAN=2" in diagnostics
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert list(install_dir.glob("*.uninstall-*")) == []
    if scan_failure == "transient":
        assert not app_root.exists()
        assert not launcher.exists()
        assert not install_lock.exists()
        assert not data_dir.exists()
        return
    assert launcher.read_bytes() == launcher_before
    assert install_lock.is_file()
    assert app_root.is_dir()
    assert {
        path.relative_to(app_root): path.read_bytes()
        for path in app_root.rglob("*")
        if path.is_file()
    } == before
    assert list(install_dir.glob("*.uninstall-*")) == []
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert data_file.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deferred cleanup only")
def test_managed_uninstall_second_data_preparation_failure_restores_all_targets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tests.cli.test_install_ps1_onedir import _fake_opensre_executable

    install_dir = tmp_path / "uninstall removal failure"
    app_root = install_dir / ".opensre-app"
    version_dir = app_root / "versions" / "build-1"
    (version_dir / "_internal").mkdir(parents=True)
    executable = version_dir / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), executable)
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("build-1\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text(
        "@echo off\n"
        ":: OpenSRE Windows launcher v1\n"
        '"%~dp0.opensre-app\\versions\\build-1\\opensre.exe" %*\n',
        encoding="utf-8",
    )
    launcher_before = launcher.read_bytes()
    install_lock = install_dir / ".opensre-app.install.lock"
    install_lock.write_bytes(b"")
    unrelated = install_dir / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    data_dirs = [tmp_path / "first removal failure data", tmp_path / "second removal failure data"]
    for index, data_dir in enumerate(data_dirs):
        data_dir.mkdir()
        (data_dir / "state.json").write_text(f"keep-{index}", encoding="utf-8")
    rollback_ready = tmp_path / "install-rollback-ready"
    rollback_release = tmp_path / "install-rollback-release"

    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.read_cleanup_script",
        lambda: _inject_deletion_preparation_failure(
            _inject_install_rollback_barrier(
                read_cleanup_script(),
                app_root=app_root,
                launcher=launcher,
                ready=rollback_ready,
                release=rollback_release,
            ),
            fail_on=4,
        ),
    )
    created_cleanup_scripts: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def _mkstemp(*, prefix: str, suffix: str) -> tuple[int, str]:
        descriptor, name = real_mkstemp(prefix=prefix, suffix=suffix, dir=tmp_path)
        created_cleanup_scripts.append(Path(name))
        return descriptor, name

    monkeypatch.setattr("surfaces.cli.lifecycle.windows.cleanup.tempfile.mkstemp", _mkstemp)
    monkeypatch.setattr(
        "surfaces.cli.lifecycle.windows.cleanup.windows_process_identity",
        _missing_process_identity,
    )
    workers = _capture_cleanup_workers(monkeypatch)

    ok, error = schedule_windows_managed_cleanup(
        executable=executable,
        app_root=app_root,
        launcher=launcher,
        parent_pid=2_147_483_647,
        data_paths=data_dirs,
    )
    assert ok is True
    assert error is None
    assert len(created_cleanup_scripts) == 1
    deadline = time.monotonic() + 90
    try:
        while not rollback_ready.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert rollback_ready.is_file()
        assert app_root.is_dir()
        assert executable.is_file()
        assert not launcher.exists()
    finally:
        rollback_release.write_text("continue", encoding="utf-8")
    output, _ = workers[0].communicate(timeout=90)
    assert workers[0].returncode == 1, output.decode("utf-8", errors="replace")
    assert not created_cleanup_scripts[0].exists()
    assert app_root.is_dir()
    assert executable.is_file()
    assert launcher.read_bytes() == launcher_before
    launched = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/c", str(launcher), "--version"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert launched.returncode == 0, launched.stdout + launched.stderr
    for index, data_dir in enumerate(data_dirs):
        assert (data_dir / "state.json").read_text(encoding="utf-8") == f"keep-{index}"
        assert list(tmp_path.glob(f"{data_dir.name}.uninstall-*")) == []
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert install_lock.is_file()
    assert list(install_dir.glob("*.uninstall-*")) == []


def test_run_uninstall_dir_removal_error_sets_exit_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    d = tmp_path / "locked_dir"
    d.mkdir()
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._data_dirs", lambda: [d])
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._is_binary_install", lambda: False)
    monkeypatch.setattr("surfaces.cli.lifecycle.uninstall._pip_uninstall", lambda: 0)

    def _fail(path: str) -> None:
        raise OSError("Permission denied")

    monkeypatch.setattr("shutil.rmtree", _fail)

    rc = run_uninstall(yes=True)

    assert rc == 1
    assert "errors" in capsys.readouterr().err


def test_uninstall_command_registered() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["uninstall", "--help"])
    assert result.exit_code == 0
    assert "uninstall" in result.output.lower()


def test_uninstall_command_yes_flag_skips_prompt() -> None:
    runner = CliRunner()

    with (
        patch("surfaces.cli.lifecycle.uninstall._data_dirs", return_value=[]),
        patch("surfaces.cli.lifecycle.uninstall._is_binary_install", return_value=False),
        patch("surfaces.cli.lifecycle.uninstall._pip_uninstall", return_value=0),
    ):
        result = runner.invoke(cli, ["uninstall", "--yes"])

    assert result.exit_code == 0
    assert "opensre has been uninstalled" in result.output


def test_uninstall_command_short_yes_flag() -> None:
    runner = CliRunner()

    with (
        patch("surfaces.cli.lifecycle.uninstall._data_dirs", return_value=[]),
        patch("surfaces.cli.lifecycle.uninstall._is_binary_install", return_value=False),
        patch("surfaces.cli.lifecycle.uninstall._pip_uninstall", return_value=0),
    ):
        result = runner.invoke(cli, ["uninstall", "-y"])

    assert result.exit_code == 0


def test_data_dirs_includes_config_opensre_path() -> None:
    from surfaces.cli.lifecycle.uninstall import _data_dirs

    paths = _data_dirs()
    path_strs = [str(p) for p in paths]
    assert any(".opensre" in s for s in path_strs), "main ~/.opensre path missing"
    assert any(".config" in s and "opensre" in s for s in path_strs), (
        "~/.config/opensre cleanup path missing"
    )


def test_uninstall_help_describes_command() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["uninstall", "--help"])
    assert result.exit_code == 0
    assert "Remove opensre and all local data from this machine." in result.output
