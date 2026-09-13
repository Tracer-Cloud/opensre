"""Windows integration tests for the versioned onedir installer layout."""

from __future__ import annotations

import atexit
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from config.constants.installer import POWERSHELL_MODULE_PATH_ENV
from surfaces.cli.lifecycle.windows import schedule_windows_managed_cleanup

INSTALL_PS1 = Path(__file__).parents[2] / "install.ps1"
_RESULT_PREFIX = "__OPENSRE_INSTALL_RESULT__"
_PROBE_PREFIX = "__OPENSRE_LAUNCHER_PROBE__"
_CONTEXT_PREFIX = "__OPENSRE_INSTALL_CONTEXT__"
_CLEANUP_PREFIX = "__OPENSRE_CLEANUP__"
# A cold detached PowerShell host compiles the embedded C# under antivirus scanning.
_DETACHED_CLEANUP_TIMEOUT = 90
_FAKE_BINARY_ROOT: Path | None = None
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
_PARTIAL_PROCESS_ENUMERATOR = r"""
function Get-Process {
    [CmdletBinding()]
    param([int]$Id, [string]$Name)
    if ($PSBoundParameters.ContainsKey('Id')) {
        return Microsoft.PowerShell.Management\Get-Process @PSBoundParameters
    }
    $process = [pscustomobject]@{ ProcessName = 'opensre' }
    $process | Add-Member -MemberType ScriptProperty -Name Path -Value {
        throw 'forced process path failure'
    }
    return $process
}
"""


def _inject_failed_process_enumerator(source: str, *, preference: str) -> str:
    anchor = f"$ErrorActionPreference = {preference}\n"
    assert source.count(anchor) == 1
    return source.replace(anchor, anchor + _FAILED_PROCESS_ENUMERATOR, 1)


def _inject_owned_process_enumerator(
    source: str,
    *,
    process_id: int,
    process_started: str,
    second_scan_ready: Path | None = None,
    parent_observed: Path | None = None,
) -> str:
    anchor = '$ErrorActionPreference = "SilentlyContinue"\n'
    assert source.count(anchor) == 1
    assert process_started.isdigit()
    scan_barrier = ""
    if second_scan_ready is not None:
        ready_payload = base64.b64encode(str(second_scan_ready).encode("utf-8")).decode("ascii")
        scan_barrier = f"""
    $script:OpenSreTestProcessScanCount += 1
    if ($script:OpenSreTestProcessScanCount -ge 2 -and $null -ne $process) {{
        $readyPath = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String('{ready_payload}')
        )
        [System.IO.File]::WriteAllText($readyPath, 'ready')
        $scanDeadline = [System.DateTime]::UtcNow.AddSeconds(90)
        while ($null -ne $process -and [System.DateTime]::UtcNow -lt $scanDeadline) {{
            $process.Dispose()
            Start-Sleep -Milliseconds 25
            $process = Get-OpenSreOwnedTestProcess
        }}
    }}
"""
    process_override = rf"""
$script:OpenSreTestProcessScanCount = 0
function Get-OpenSreOwnedTestProcess {{
    $process = Microsoft.PowerShell.Management\Get-Process `
        -Id {process_id} `
        -ErrorAction SilentlyContinue
    if ($null -eq $process) {{
        return $null
    }}
    try {{
        $started = $process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(
            [System.Globalization.CultureInfo]::InvariantCulture
        )
        if ($started -cne '{process_started}') {{
            $process.Dispose()
            return $null
        }}
    }}
    catch {{
        $process.Dispose()
        return $null
    }}
    return $process
}}

function Get-Process {{
    [CmdletBinding()]
    param([int]$Id, [string]$Name)
    if ($PSBoundParameters.ContainsKey('Id')) {{
        return Microsoft.PowerShell.Management\Get-Process @PSBoundParameters
    }}
    $process = Get-OpenSreOwnedTestProcess
{scan_barrier}
    if ($null -ne $process) {{
        return $process
    }}
}}
"""
    injected = source.replace(anchor, anchor + process_override, 1)
    if parent_observed is None:
        return injected

    observed_payload = base64.b64encode(str(parent_observed).encode("utf-8")).decode("ascii")
    state_anchor = "        $parentState = Get-OpenSreParentIdentityState\n"
    assert injected.count(state_anchor) == 1
    parent_observed_probe = f"""        if ($parentState -ceq 'running') {{
            $observedPath = [System.Text.Encoding]::UTF8.GetString(
                [System.Convert]::FromBase64String('{observed_payload}')
            )
            [System.IO.File]::WriteAllText($observedPath, 'observed')
        }}
"""
    return injected.replace(state_anchor, state_anchor + parent_observed_probe, 1)


def _write_scoped_cleanup_installer(
    root: Path,
    *,
    process_id: int,
    process_started: str,
    second_scan_ready: Path | None = None,
    parent_observed: Path | None = None,
) -> Path:
    installer = root / "install-with-scoped-cleanup-process.ps1"
    installer.write_text(
        _inject_owned_process_enumerator(
            INSTALL_PS1.read_text(encoding="utf-8"),
            process_id=process_id,
            process_started=process_started,
            second_scan_ready=second_scan_ready,
            parent_observed=parent_observed,
        ),
        encoding="utf-8",
    )
    return installer


def _inject_parent_with_readable_metadata(
    source: str,
    *,
    executable: Path,
    started: int,
    has_exited: str,
) -> str:
    anchor = '$ErrorActionPreference = "SilentlyContinue"\n'
    assert source.count(anchor) == 1
    property_body = {
        "true": "return $true",
        "false": "return $false",
        "non-bool": "return 'true'",
        "throw": "throw 'forced HasExited inspection failure'",
    }[has_exited]
    executable_payload = base64.b64encode(str(executable).encode("utf-8")).decode("ascii")
    process_override = rf"""
function Get-Process {{
    [CmdletBinding()]
    param([int]$Id, [string]$Name)
    if (-not $PSBoundParameters.ContainsKey('Id')) {{ return @() }}
    $path = [System.Text.Encoding]::UTF8.GetString(
        [System.Convert]::FromBase64String('{executable_payload}')
    )
    $parent = [pscustomobject]@{{
        ProcessName = 'opensre'
        Path = $path
        StartTime = [System.DateTime]::FromFileTimeUtc({started})
    }}
    $parent | Add-Member -MemberType ScriptProperty -Name HasExited -Value {{
        {property_body}
    }}
    return $parent
}}
"""
    return source.replace(anchor, anchor + process_override, 1)


def _inject_exited_process_with_unavailable_path(
    source: str,
    *,
    busy_executable: Path | None = None,
) -> str:
    anchor = '$ErrorActionPreference = "SilentlyContinue"\n'
    assert source.count(anchor) == 1
    busy_process = ""
    if busy_executable is not None:
        busy_payload = base64.b64encode(str(busy_executable).encode("utf-8")).decode("ascii")
        busy_process = f"""
    $busyPath = [System.Text.Encoding]::UTF8.GetString(
        [System.Convert]::FromBase64String('{busy_payload}')
    )
    $processes += [pscustomobject]@{{
        ProcessName = 'opensre'
        HasExited = $false
        Path = $busyPath
    }}
"""
    process_override = r"""
function Get-Process {
    [CmdletBinding()]
    param([int]$Id, [string]$Name)
    if ($PSBoundParameters.ContainsKey('Id')) { return $null }
    $exited = [pscustomobject]@{
        ProcessName = 'opensre'
        HasExited = $true
    }
    $exited | Add-Member -MemberType ScriptProperty -Name Path -Value {
        throw 'forced exited-process path failure'
    }
    $processes = @($exited)
__BUSY_PROCESS__
    return $processes
}
"""
    process_override = process_override.replace("__BUSY_PROCESS__", busy_process)
    return source.replace(anchor, anchor + process_override, 1)


def _inject_process_exit_after_target_match(source: str, *, executable: Path) -> str:
    anchor = '$ErrorActionPreference = "SilentlyContinue"\n'
    assert source.count(anchor) == 1
    executable_payload = base64.b64encode(str(executable).encode("utf-8")).decode("ascii")
    process_override = rf"""
$script:OpenSreTestHasExitedChecks = 0
function Get-Process {{
    [CmdletBinding()]
    param([int]$Id, [string]$Name)
    if ($PSBoundParameters.ContainsKey('Id')) {{ return $null }}
    $path = [System.Text.Encoding]::UTF8.GetString(
        [System.Convert]::FromBase64String('{executable_payload}')
    )
    $process = [pscustomobject]@{{
        ProcessName = 'opensre'
        Path = $path
    }}
    $process | Add-Member -MemberType ScriptProperty -Name HasExited -Value {{
        $script:OpenSreTestHasExitedChecks += 1
        return $script:OpenSreTestHasExitedChecks -ge 2
    }}
    return $process
}}
"""
    return source.replace(anchor, anchor + process_override, 1)


def _inject_process_with_unusable_name(source: str) -> str:
    anchor = '$ErrorActionPreference = "SilentlyContinue"\n'
    assert source.count(anchor) == 1
    process_override = r"""
function Get-Process {
    [CmdletBinding()]
    param([int]$Id, [string]$Name)
    if ($PSBoundParameters.ContainsKey('Id')) { return $null }
    return [pscustomobject]@{
        ProcessName = $null
        HasExited = $false
        Path = $null
    }
}
"""
    return source.replace(anchor, anchor + process_override, 1)


def _inject_parent_exit_during_metadata(source: str, *, started: int) -> str:
    anchor = '$ErrorActionPreference = "SilentlyContinue"\n'
    assert source.count(anchor) == 1
    process_override = rf"""
$script:OpenSreTestHasExitedChecks = 0
function Get-Process {{
    [CmdletBinding()]
    param([int]$Id, [string]$Name)
    if (-not $PSBoundParameters.ContainsKey('Id')) {{ return @() }}
    $parent = [pscustomobject]@{{
        ProcessName = 'opensre'
        StartTime = [System.DateTime]::FromFileTimeUtc({started})
    }}
    $parent | Add-Member -MemberType ScriptProperty -Name HasExited -Value {{
        $script:OpenSreTestHasExitedChecks += 1
        return $script:OpenSreTestHasExitedChecks -ge 2
    }}
    $parent | Add-Member -MemberType ScriptProperty -Name Path -Value {{
        throw 'forced parent exit during metadata inspection'
    }}
    return $parent
}}
"""
    return source.replace(anchor, anchor + process_override, 1)


def _inject_cleanup_lock_open_barrier(
    source: str,
    *,
    ready_path: Path,
    continue_path: Path,
) -> str:
    anchor = "$lockHandle = $null\n$lockDeadline = [System.DateTime]::UtcNow.AddSeconds(30)\n"
    assert source.count(anchor) == 1
    ready_payload = base64.b64encode(str(ready_path).encode("utf-8")).decode("ascii")
    continue_payload = base64.b64encode(str(continue_path).encode("utf-8")).decode("ascii")
    barrier = rf"""
$openSreTestReadyPath = [System.Text.Encoding]::UTF8.GetString(
    [System.Convert]::FromBase64String('{ready_payload}')
)
$openSreTestContinuePath = [System.Text.Encoding]::UTF8.GetString(
    [System.Convert]::FromBase64String('{continue_payload}')
)
[System.IO.File]::WriteAllText($openSreTestReadyPath, 'ready')
while (-not [System.IO.File]::Exists($openSreTestContinuePath)) {{
    Start-Sleep -Milliseconds 25
}}
"""
    return source.replace(anchor, barrier + anchor, 1)


def _set_embedded_cleanup_lock_timeout(source: str, *, seconds: int) -> str:
    anchor = "$lockDeadline = [System.DateTime]::UtcNow.AddSeconds(30)"
    assert source.count(anchor) == 1
    return source.replace(
        anchor,
        f"$lockDeadline = [System.DateTime]::UtcNow.AddSeconds({seconds})",
        1,
    )


def _set_embedded_cleanup_parent_timeout(source: str, *, seconds: int) -> str:
    anchor = "$waitDeadline = [System.DateTime]::UtcNow.AddMinutes(10)"
    assert source.count(anchor) == 1
    return source.replace(
        anchor,
        f"$waitDeadline = [System.DateTime]::UtcNow.AddSeconds({seconds})",
        1,
    )


def _inject_uncertain_parent_metadata(source: str) -> str:
    anchor = '$ErrorActionPreference = "SilentlyContinue"\n'
    assert source.count(anchor) == 1
    process_override = r"""
function Get-Process {
    [CmdletBinding()]
    param([int]$Id, [string]$Name)
    if (-not $PSBoundParameters.ContainsKey('Id')) {
        return @()
    }
    $process = [pscustomobject]@{
        ProcessName = 'opensre'
        StartTime = [System.DateTime]::UtcNow
        HasExited = $false
    }
    $process | Add-Member -MemberType ScriptProperty -Name Path -Value {
        throw 'forced parent path inspection race'
    }
    return $process
}
"""
    return source.replace(anchor, anchor + process_override, 1)


def _inject_retired_parent_path_metadata_error(source: str) -> str:
    anchor = '$ErrorActionPreference = "SilentlyContinue"\n'
    assert source.count(anchor) == 1
    process_override = r"""
function Get-Process {
    [CmdletBinding()]
    param([int]$Id, [string]$Name)
    if (-not $PSBoundParameters.ContainsKey('Id')) {
        return @()
    }
    $nativeProcess = Microsoft.PowerShell.Management\Get-Process `
        -Id $Id `
        -ErrorAction Stop
    try {
        $process = [pscustomobject]@{
            StartTime = $nativeProcess.StartTime
            HasExited = $false
        }
        $process | Add-Member -MemberType ScriptProperty -Name Path -Value {
            throw 'forced retired parent path metadata error'
        }
        return $process
    }
    finally {
        $nativeProcess.Dispose()
    }
}
"""
    return source.replace(anchor, anchor + process_override, 1)


def _inject_late_cleanup_launch_probe(source: str, *, marker_path: Path) -> str:
    anchor = "                $retiredRecord.DeletionLease.PrepareTree()\n"
    assert source.count(anchor) == 1
    marker_payload = base64.b64encode(str(marker_path).encode("utf-8")).decode("ascii")
    probe = rf"""        $openSreLateLaunchMarker = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String('{marker_payload}')
        )
        $openSreLateProcess = $null
        try {{
            $openSreLateProcess = Start-Process `
                -FilePath (Join-Path ([string]$retiredRecord.Path) 'opensre.exe') `
                -ArgumentList @('hold', '30000') `
                -PassThru `
                -ErrorAction Stop
            [System.IO.File]::WriteAllText($openSreLateLaunchMarker, 'launched')
        }}
        catch {{
            [System.IO.File]::WriteAllText($openSreLateLaunchMarker, 'blocked')
        }}
        finally {{
            if ($null -ne $openSreLateProcess) {{
                try {{
                    Stop-Process -Id $openSreLateProcess.Id -Force -ErrorAction SilentlyContinue
                    $openSreLateProcess.WaitForExit(5000) | Out-Null
                }}
                catch {{
                    # The test process may already have exited.
                }}
                Close-OpenSreCleanupProcess -Process $openSreLateProcess
            }}
        }}
"""
    return source.replace(anchor, probe + anchor, 1)


def _inject_late_cleanup_layout_swap(
    source: str,
    *,
    preserved_layout: Path,
    outside_layout: Path,
) -> str:
    anchor = """        $retiredPath = Join-Path $LayoutRoot (
            "retired-$([System.Guid]::NewGuid().ToString('N'))"
        )
"""
    assert source.count(anchor) == 1
    preserved_payload = base64.b64encode(str(preserved_layout).encode()).decode()
    outside_payload = base64.b64encode(str(outside_layout).encode()).decode()
    swap = rf"""        $openSrePreservedLayout = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String('{preserved_payload}')
        )
        $openSreOutsideLayout = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String('{outside_payload}')
        )
        [System.IO.Directory]::Move(
            (ConvertTo-OpenSreExtendedPath -Path $LayoutRoot),
            (ConvertTo-OpenSreExtendedPath -Path $openSrePreservedLayout)
        )
        New-Item `
            -ItemType Junction `
            -Path $LayoutRoot `
            -Target $openSreOutsideLayout | Out-Null
"""
    return source.replace(anchor, swap + anchor, 1)


def _inject_cleanup_deletion_target_swap(
    source: str,
    *,
    preserved_target: Path,
    marker_path: Path,
) -> str:
    anchor = "                $retiredRecord.DeletionLease.PrepareTree()\n"
    assert source.count(anchor) == 1
    preserved_payload = base64.b64encode(str(preserved_target).encode()).decode()
    marker_payload = base64.b64encode(str(marker_path).encode()).decode()
    swap = rf"""                $openSrePreservedTarget = [System.Text.Encoding]::UTF8.GetString(
                    [System.Convert]::FromBase64String('{preserved_payload}')
                )
                $openSreDeletionMarker = [System.Text.Encoding]::UTF8.GetString(
                    [System.Convert]::FromBase64String('{marker_payload}')
                )
                try {{
                    [System.IO.Directory]::Move(
                        (ConvertTo-OpenSreExtendedPath -Path ([string]$retiredRecord.Path)),
                        (ConvertTo-OpenSreExtendedPath -Path $openSrePreservedTarget)
                    )
                    [System.IO.Directory]::CreateDirectory(
                        (ConvertTo-OpenSreExtendedPath -Path ([string]$retiredRecord.Path))
                    ) | Out-Null
                    [System.IO.File]::WriteAllText(
                        (Join-Path ([string]$retiredRecord.Path) 'user-data.txt'),
                        'preserve'
                    )
                    [System.IO.File]::WriteAllText($openSreDeletionMarker, 'swapped')
                }}
                catch {{
                    [System.IO.File]::WriteAllText($openSreDeletionMarker, 'blocked')
                }}
"""
    return source.replace(anchor, swap + anchor, 1)


def _powershell() -> str | None:
    """Resolve Windows PowerShell, which is the interpreter the product itself uses.

    ``install.ps1`` is only ever invoked through Windows PowerShell (``opensre
    update`` and the documented ``irm | iex`` command), so the tests exercise it
    there too. PowerShell 7 is also unusable here: ``Add-Type -OutputType
    ConsoleApplication`` builds the fake ``opensre.exe`` and is unsupported on
    .NET Core.
    """
    system_root = os.environ.get("SYSTEMROOT")
    if system_root:
        candidate = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("powershell.exe")


_POWERSHELL = _powershell()
pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or _POWERSHELL is None,
    reason="The versioned onedir installer contract requires Windows PowerShell.",
)


def _ps_literal(value: str | Path) -> str:
    """Return a single-quoted PowerShell literal."""
    return "'" + str(value).replace("'", "''") + "'"


def _powershell_env() -> dict[str, str]:
    env = os.environ.copy()
    env["OPENSRE_AUTO_LAUNCH"] = "0"
    env["OPENSRE_SKIP_GH_INSTALL"] = "1"
    # A PSModulePath inherited from a different host - PowerShell 7 on a CI runner -
    # can stop Windows PowerShell resolving its own modules, which makes install.ps1
    # fail on Get-FileHash and Expand-Archive. Pin the interpreter's own locations.
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    for name in tuple(env):
        if name.casefold() == POWERSHELL_MODULE_PATH_ENV.casefold():
            del env[name]
    env[POWERSHELL_MODULE_PATH_ENV] = os.pathsep.join(
        [
            str(Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "Modules"),
            str(Path(program_files) / "WindowsPowerShell" / "Modules"),
        ]
    )
    return env


def _run_powershell(script: str, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    assert _POWERSHELL is not None
    return subprocess.run(
        [
            _POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        cwd=cwd,
        env=_powershell_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )


def _schedule_deferred_cleanup(
    *,
    installer_path: Path,
    layout_root: Path,
    target: Path,
    cwd: Path,
    parent_process_id: int = 0,
    parent_executable_path: Path | None = None,
    parent_started: str = "",
) -> tuple[subprocess.CompletedProcess[str], Path | None]:
    parent_executable = parent_executable_path or ""
    completed = _run_powershell(
        f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(installer_path)} -SkipMain
$cleanup = Start-OpenSreDeferredCleanup `
    -LayoutRoot {_ps_literal(layout_root)} `
    -TargetPaths @({_ps_literal(target)}) `
    -ParentProcessId {parent_process_id} `
    -ParentExecutablePath {_ps_literal(parent_executable)} `
    -ParentStarted {_ps_literal(parent_started)}
Write-Output ({_ps_literal(_CLEANUP_PREFIX)} + [string]$cleanup)
""",
        cwd=cwd,
    )
    cleanup_paths = [
        Path(line.removeprefix(_CLEANUP_PREFIX))
        for line in completed.stdout.splitlines()
        if line.startswith(_CLEANUP_PREFIX)
    ]
    cleanup_path = cleanup_paths[0] if len(cleanup_paths) == 1 else None
    return completed, cleanup_path


def _fake_opensre_executable() -> Path:
    global _FAKE_BINARY_ROOT

    if _FAKE_BINARY_ROOT is not None:
        executable = _FAKE_BINARY_ROOT / "opensre.exe"
        if executable.is_file():
            return executable

    assert _POWERSHELL is not None
    root = Path(tempfile.mkdtemp(prefix="opensre-installer-test-binary-"))
    executable = root / "opensre.exe"
    source = r"""
using System;
using System.Threading;

public static class Program
{
    public static int Main(string[] args)
    {
        string guardedExecutable = Environment.GetEnvironmentVariable(
            "OPENSRE_TEST_GUARDED_EXECUTABLE"
        );
        string executionMarker = Environment.GetEnvironmentVariable(
            "OPENSRE_TEST_EXECUTION_MARKER"
        );
        if (!String.IsNullOrEmpty(guardedExecutable) &&
            !String.IsNullOrEmpty(executionMarker))
        {
            string runningExecutable = System.Reflection.Assembly.GetExecutingAssembly().Location;
            if (String.Equals(
                    System.IO.Path.GetFullPath(guardedExecutable),
                    System.IO.Path.GetFullPath(runningExecutable),
                    StringComparison.OrdinalIgnoreCase
                ))
            {
                System.IO.File.WriteAllText(executionMarker, "executed");
            }
        }
        if (args.Length > 0 && args[0] == "--version")
        {
            Console.WriteLine("opensre, version 0.1.2026.8.31");
            return 0;
        }
        if (args.Length > 0 && args[0] == "_package-smoke")
        {
            string failureMarker = System.IO.Path.Combine(
                AppContext.BaseDirectory,
                "_internal",
                "package-smoke-fail.txt"
            );
            if (System.IO.File.Exists(failureMarker))
            {
                Console.WriteLine("{\"status\":\"failed\"}");
                return 1;
            }
            Console.WriteLine("{\"status\":\"ok\"}");
            return 0;
        }
        if (args.Length > 0 && args[0] == "hold")
        {
            int milliseconds = args.Length > 1 ? Int32.Parse(args[1]) : 30000;
            Thread.Sleep(milliseconds);
            return 0;
        }
        if (args.Length > 1 && args[0] == "hold-until")
        {
            if (args.Length > 2)
            {
                System.IO.File.WriteAllText(args[2], "ready");
            }
            while (!System.IO.File.Exists(args[1]))
            {
                Thread.Sleep(25);
            }
            return 0;
        }
        for (int index = 0; index < args.Length; index++)
        {
            if (String.Equals(args[index], "echo", StringComparison.OrdinalIgnoreCase))
            {
                Console.WriteLine(String.Join(" ", args, index + 1, args.Length - index - 1));
                return 0;
            }
            if (String.Equals(args[index], "exit", StringComparison.OrdinalIgnoreCase) &&
                index + 1 < args.Length)
            {
                return Int32.Parse(args[index + 1]);
            }
            if (String.Equals(args[index], "ping", StringComparison.OrdinalIgnoreCase))
            {
                int seconds = 3;
                for (int pingIndex = index + 1; pingIndex + 1 < args.Length; pingIndex++)
                {
                    if (args[pingIndex] == "-n")
                    {
                        seconds = Math.Max(1, Int32.Parse(args[pingIndex + 1]) - 1);
                        break;
                    }
                }
                Thread.Sleep(seconds * 1000);
                return 0;
            }
        }
        return 0;
    }
}
"""
    source_payload = base64.b64encode(source.encode("utf-8")).decode("ascii")
    completed = _run_powershell(
        f"""
$source = [System.Text.Encoding]::UTF8.GetString(
    [System.Convert]::FromBase64String('{source_payload}')
)
Add-Type `
    -TypeDefinition $source `
    -Language CSharp `
    -OutputAssembly {_ps_literal(executable)} `
    -OutputType ConsoleApplication
""",
        cwd=root,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert executable.is_file()
    _FAKE_BINARY_ROOT = root
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    return executable


def _prefixed_json(stdout: str, prefix: str) -> dict[str, Any]:
    matches = [line[len(prefix) :] for line in stdout.splitlines() if line.startswith(prefix)]
    assert len(matches) == 1, f"missing {prefix!r} payload in output:\n{stdout}"
    payload = json.loads(matches[0])
    assert isinstance(payload, dict)
    return payload


def _install_bundle(
    *,
    binary_path: Path,
    install_dir: Path,
    install_id: str,
    cwd: Path,
    parent_process_id: int = 0,
    parent_executable_path: Path | None = None,
    parent_started: str = "",
    verified_legacy_binary_path: Path | None = None,
    approved_legacy_binary_path: Path | None = None,
    approved_legacy_binary_sha256: str = "",
    installer_override: str = "",
    installer_path: Path = INSTALL_PS1,
    check: bool = True,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any] | None]:
    verified_legacy_path = verified_legacy_binary_path or ""
    approved_legacy_path = approved_legacy_binary_path or ""
    parent_executable = parent_executable_path or ""
    script = f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(installer_path)} -SkipMain
{installer_override}
$verifiedLegacySnapshot = if ({_ps_literal(verified_legacy_path)} -and
    (Test-OpenSreInstallFileExists -Path {_ps_literal(verified_legacy_path)})) {{
    Get-OpenSreInstallFileSnapshot -Path {_ps_literal(verified_legacy_path)}
}} else {{
    $null
}}
$approvedLegacySnapshot = if ({_ps_literal(approved_legacy_path)} -and
    (Test-OpenSreInstallFileExists -Path {_ps_literal(approved_legacy_path)})) {{
    Get-OpenSreInstallFileSnapshot -Path {_ps_literal(approved_legacy_path)}
}} else {{
    $null
}}
$result = Install-OpenSreVerifiedBundle `
    -BinaryPath {_ps_literal(binary_path)} `
    -InstallDir {_ps_literal(install_dir)} `
    -InstallId {_ps_literal(install_id)} `
    -ParentProcessId {parent_process_id} `
    -ParentExecutablePath {_ps_literal(parent_executable)} `
    -ParentStarted {_ps_literal(parent_started)} `
    -VerifiedLegacyBinaryPath {_ps_literal(verified_legacy_path)} `
    -VerifiedLegacyBinarySnapshot $verifiedLegacySnapshot `
    -ApprovedLegacyBinaryPath {_ps_literal(approved_legacy_path)} `
    -ApprovedLegacyBinarySha256 {_ps_literal(approved_legacy_binary_sha256)} `
    -ApprovedLegacyBinarySnapshot $approvedLegacySnapshot
$payload = [ordered]@{{
    BinaryPath = [string]$result.BinaryPath
    LauncherPath = [string]$result.LauncherPath
    AppRoot = [string]$result.AppRoot
    LayoutRoot = if ($result.PSObject.Properties['LayoutRoot']) {{
        [string]$result.LayoutRoot
    }} else {{
        ''
    }}
    CleanupPath = if ($result.PSObject.Properties['CleanupPath']) {{
        [string]$result.CleanupPath
    }} else {{
        ''
    }}
    DeferredCleanup = [bool]$result.DeferredCleanup
}}
Write-Output ({_ps_literal(_RESULT_PREFIX)} + ($payload | ConvertTo-Json -Compress))
"""
    completed = _run_powershell(script, cwd=cwd)
    if check:
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return completed, _prefixed_json(completed.stdout, _RESULT_PREFIX)
    if completed.returncode == 0:
        return completed, _prefixed_json(completed.stdout, _RESULT_PREFIX)
    return completed, None


def _install_onefile(
    *,
    binary_path: Path,
    install_dir: Path,
    cwd: Path,
    verified_legacy_binary_path: Path | None = None,
    approved_legacy_binary_path: Path | None = None,
    approved_legacy_binary_sha256: str = "",
    installer_override: str = "",
    check: bool = True,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any] | None]:
    verified_legacy_path = verified_legacy_binary_path or ""
    approved_legacy_path = approved_legacy_binary_path or ""
    script = f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(INSTALL_PS1)} -SkipMain
{installer_override}
$verifiedLegacySnapshot = if ({_ps_literal(verified_legacy_path)} -and
    (Test-OpenSreInstallFileExists -Path {_ps_literal(verified_legacy_path)})) {{
    Get-OpenSreInstallFileSnapshot -Path {_ps_literal(verified_legacy_path)}
}} else {{
    $null
}}
$approvedLegacySnapshot = if ({_ps_literal(approved_legacy_path)} -and
    (Test-OpenSreInstallFileExists -Path {_ps_literal(approved_legacy_path)})) {{
    Get-OpenSreInstallFileSnapshot -Path {_ps_literal(approved_legacy_path)}
}} else {{
    $null
}}
$result = Install-OpenSreVerifiedOnefile `
    -BinaryPath {_ps_literal(binary_path)} `
    -InstallDir {_ps_literal(install_dir)} `
    -VerifiedLegacyBinaryPath {_ps_literal(verified_legacy_path)} `
    -VerifiedLegacyBinarySnapshot $verifiedLegacySnapshot `
    -ApprovedLegacyBinaryPath {_ps_literal(approved_legacy_path)} `
    -ApprovedLegacyBinarySha256 {_ps_literal(approved_legacy_binary_sha256)} `
    -ApprovedLegacyBinarySnapshot $approvedLegacySnapshot
$payload = [ordered]@{{
    BinaryPath = [string]$result.BinaryPath
    LauncherPath = [string]$result.LauncherPath
    AppRoot = [string]$result.AppRoot
    LayoutRoot = [string]$result.LayoutRoot
    CleanupPath = [string]$result.CleanupPath
    DeferredCleanup = [bool]$result.DeferredCleanup
}}
Write-Output ({_ps_literal(_RESULT_PREFIX)} + ($payload | ConvertTo-Json -Compress))
"""
    completed = _run_powershell(script, cwd=cwd)
    if check:
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return completed, _prefixed_json(completed.stdout, _RESULT_PREFIX)
    if completed.returncode == 0:
        return completed, _prefixed_json(completed.stdout, _RESULT_PREFIX)
    return completed, None


def _make_onedir_bundle(
    root: Path,
    *,
    payload: dict[str, str] | None = None,
) -> Path:
    app_root = root / "opensre"
    internal = app_root / "_internal"
    internal.mkdir(parents=True)
    binary = app_root / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), binary)
    for relative_name, content in (payload or {"payload.txt": "bundled"}).items():
        destination = internal / relative_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    return binary


def _make_invalid_onedir_bundle(root: Path) -> Path:
    app_root = root / "opensre"
    internal = app_root / "_internal"
    internal.mkdir(parents=True)
    (internal / "invalid-build.txt").write_text("must not activate", encoding="utf-8")
    binary = app_root / "opensre.exe"
    binary.write_bytes(b"not a Windows executable")
    return binary


def _probe_launcher(launcher: Path, *, cwd: Path) -> dict[str, Any]:
    script = f"""
$ErrorActionPreference = 'Continue'
$versionOutput = @(& {_ps_literal(launcher)} --version 2>&1) | Out-String
$versionExit = $LASTEXITCODE
$argumentOutput = @(& {_ps_literal(launcher)} /d /c echo 'value with spaces' 2>&1) | Out-String
$argumentExit = $LASTEXITCODE
& {_ps_literal(launcher)} /d /c exit 37
$forwardedExit = $LASTEXITCODE
$payload = [ordered]@{{
    VersionOutput = $versionOutput.Trim()
    VersionExit = $versionExit
    ArgumentOutput = $argumentOutput.Trim()
    ArgumentExit = $argumentExit
    ForwardedExit = $forwardedExit
}}
Write-Output ({_ps_literal(_PROBE_PREFIX)} + ($payload | ConvertTo-Json -Compress))
"""
    completed = _run_powershell(script, cwd=cwd)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return _prefixed_json(completed.stdout, _PROBE_PREFIX)


def _path(payload: dict[str, Any], key: str) -> Path:
    value = payload.get(key)
    assert isinstance(value, str) and value, f"installer result omitted {key}"
    return Path(value)


def _resolve_install_context(
    *,
    cwd: Path,
    update_executable: Path | None = None,
    explicit_install_dir: Path | None = None,
    parent_process_id: int = 0,
    parent_started: str = "",
    installer_override: str = "",
    check: bool = True,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any] | None]:
    update_line = (
        f"$env:OPENSRE_UPDATE_EXECUTABLE = {_ps_literal(update_executable)}"
        if update_executable is not None
        else "Remove-Item Env:OPENSRE_UPDATE_EXECUTABLE -ErrorAction SilentlyContinue"
    )
    install_line = (
        f"$env:OPENSRE_INSTALL_DIR = {_ps_literal(explicit_install_dir)}"
        if explicit_install_dir is not None
        else "Remove-Item Env:OPENSRE_INSTALL_DIR -ErrorAction SilentlyContinue"
    )
    parent_line = (
        f"$env:OPENSRE_UPDATE_PARENT_PID = '{parent_process_id}'"
        if parent_process_id > 0
        else "Remove-Item Env:OPENSRE_UPDATE_PARENT_PID -ErrorAction SilentlyContinue"
    )
    started_line = (
        f"$env:OPENSRE_UPDATE_PARENT_STARTED = {_ps_literal(parent_started)}"
        if parent_started
        else "Remove-Item Env:OPENSRE_UPDATE_PARENT_STARTED -ErrorAction SilentlyContinue"
    )
    script = f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(INSTALL_PS1)} -SkipMain
{installer_override}
{update_line}
{install_line}
{parent_line}
{started_line}
$context = Resolve-OpenSreInstallContext
$payload = [ordered]@{{
    InstallDir = [string]$context.InstallDir
    ParentProcessId = [int]$context.ParentProcessId
    ParentStarted = [string]$context.ParentStarted
    IsUpdate = [bool]$context.IsUpdate
    LegacyBinaryPath = [string]$context.LegacyBinaryPath
}}
Write-Output ({_ps_literal(_CONTEXT_PREFIX)} + ($payload | ConvertTo-Json -Compress))
"""
    completed = _run_powershell(script, cwd=cwd)
    if check:
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return completed, _prefixed_json(completed.stdout, _CONTEXT_PREFIX)
    if completed.returncode == 0:
        return completed, _prefixed_json(completed.stdout, _CONTEXT_PREFIX)
    return completed, None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _short_path(path: Path, *, cwd: Path) -> Path:
    completed = _run_powershell(
        f"""
. {_ps_literal(INSTALL_PS1)} -SkipMain
Initialize-OpenSreNativePathApi
[OpenSre.NativePathApi]::GetShortPath({_ps_literal(path)})
""",
        cwd=cwd,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return Path(completed.stdout.strip())


def _process_started_token(pid: int, *, cwd: Path) -> str:
    completed = _run_powershell(
        f"""
$process = Get-Process -Id {pid} -ErrorAction Stop
$process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(
    [System.Globalization.CultureInfo]::InvariantCulture
)
""",
        cwd=cwd,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout.strip()


def _wait_until(
    predicate: Callable[[], bool], *, timeout: float = _DETACHED_CLEANUP_TIMEOUT
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert predicate()


def _make_managed_cleanup_target(install_dir: Path) -> tuple[Path, Path]:
    layout_root = install_dir / ".opensre-app"
    target = layout_root / "versions" / "old-build"
    target.mkdir(parents=True)
    shutil.copy2(_fake_opensre_executable(), target / "opensre.exe")
    (layout_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (layout_root / "current.txt").write_text("active-build\n", encoding="utf-8")
    return layout_root, target


def test_onedir_bundle_survives_source_removal_and_runs_installed_version(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "download extraction"
    binary = _make_onedir_bundle(
        source_root,
        payload={"nested/payload.txt": "complete bundle"},
    )
    install_dir = tmp_path / "installed"

    _, result = _install_bundle(
        binary_path=binary,
        install_dir=install_dir,
        install_id="build-one",
        cwd=tmp_path,
    )
    assert result is not None
    installed_binary = _path(result, "BinaryPath")
    launcher = _path(result, "LauncherPath")
    app_root = _path(result, "AppRoot")

    shutil.rmtree(source_root)

    assert installed_binary == app_root / "opensre.exe"
    assert installed_binary.is_file()
    assert launcher.is_file()
    assert (app_root / "_internal" / "nested" / "payload.txt").read_text(
        encoding="utf-8"
    ) == "complete bundle"
    probe = _probe_launcher(launcher, cwd=tmp_path)
    assert probe["VersionExit"] == 0
    assert probe["VersionOutput"]


def test_legacy_flat_onefile_is_removed_during_migration(tmp_path: Path) -> None:
    install_dir = tmp_path / "bin"
    install_dir.mkdir()
    legacy_binary = install_dir / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), legacy_binary)
    legacy_hash = _sha256(legacy_binary)
    sentinel = install_dir / "unrelated-tool.txt"
    sentinel.write_text("keep me", encoding="utf-8")
    binary = _make_onedir_bundle(tmp_path / "new bundle")

    _, result = _install_bundle(
        binary_path=binary,
        install_dir=install_dir,
        install_id="migrated-build",
        cwd=tmp_path,
        verified_legacy_binary_path=legacy_binary,
    )
    assert result is not None
    launcher = _path(result, "LauncherPath")

    assert not legacy_binary.exists()
    retired = list((install_dir / ".opensre-app").glob("retired-*"))
    assert retired
    assert all(_sha256(path) == legacy_hash for path in retired)
    assert _path(result, "BinaryPath").parent != install_dir
    assert sentinel.read_text(encoding="utf-8") == "keep me"
    assert _probe_launcher(launcher, cwd=tmp_path)["VersionExit"] == 0


def test_installer_never_executes_unverified_preexisting_flat_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_dir = tmp_path / "unverified legacy executable"
    install_dir.mkdir()
    preexisting_binary = install_dir / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), preexisting_binary)
    original_hash = _sha256(preexisting_binary)
    execution_marker = tmp_path / "preexisting-executable-ran.txt"
    monkeypatch.setenv("OPENSRE_TEST_GUARDED_EXECUTABLE", str(preexisting_binary))
    monkeypatch.setenv("OPENSRE_TEST_EXECUTION_MARKER", str(execution_marker))
    replacement = _make_onedir_bundle(tmp_path / "unverified replacement")

    completed, result = _install_bundle(
        binary_path=replacement,
        install_dir=install_dir,
        install_id="must-not-run-preexisting",
        cwd=tmp_path,
        check=False,
    )

    assert completed.returncode != 0
    assert result is None
    assert "Refusing to replace unverified pre-existing executable" in completed.stderr
    assert not execution_marker.exists()
    assert preexisting_binary.is_file()
    assert _sha256(preexisting_binary) == original_hash
    assert not (install_dir / "opensre.cmd").exists()
    assert not (install_dir / ".opensre-app" / "current.txt").exists()


def test_onedir_migration_refuses_legacy_target_swapped_after_authorization(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "onedir post-authorization swap"
    install_dir.mkdir()
    legacy_binary = install_dir / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), legacy_binary)
    approved_hash = _sha256(legacy_binary)
    preserved_approved = install_dir / "approved-opensre.exe"
    unrelated_bytes = b"unrelated late legacy target"
    unrelated_payload = base64.b64encode(unrelated_bytes).decode("ascii")
    replacement = _make_onedir_bundle(tmp_path / "onedir swap replacement")
    override = f"""
$script:OpenSreOriginalTestInstallFileSnapshot = ${{function:Test-OpenSreInstallFileSnapshot}}
$script:OpenSreLegacyTargetSwapped = $false
function Test-OpenSreInstallFileSnapshot {{
    param([string]$Path, [object]$Expected, [switch]$AllowRelocated)
    $result = & $script:OpenSreOriginalTestInstallFileSnapshot `
        -Path $Path `
        -Expected $Expected `
        -AllowRelocated:$AllowRelocated
    if ($result -and -not $AllowRelocated -and
        -not $script:OpenSreLegacyTargetSwapped -and
        (Test-OpenSreSamePath -Left $Path -Right {_ps_literal(legacy_binary)})) {{
        $script:OpenSreLegacyTargetSwapped = $true
        [System.IO.File]::Move(
            {_ps_literal(legacy_binary)},
            {_ps_literal(preserved_approved)}
        )
        [System.IO.File]::WriteAllBytes(
            {_ps_literal(legacy_binary)},
            [System.Convert]::FromBase64String('{unrelated_payload}')
        )
    }}
    return $result
}}
"""

    completed, result = _install_bundle(
        binary_path=replacement,
        install_dir=install_dir,
        install_id="must-not-retire-swapped-legacy",
        cwd=tmp_path,
        approved_legacy_binary_path=legacy_binary,
        approved_legacy_binary_sha256=approved_hash,
        installer_override=override,
        check=False,
    )

    assert completed.returncode != 0
    assert result is None
    assert "Refusing to replace unverified pre-existing executable" in completed.stderr
    assert legacy_binary.read_bytes() == unrelated_bytes
    assert _sha256(preserved_approved) == approved_hash
    assert not (install_dir / "opensre.cmd").exists()
    assert not (install_dir / ".opensre-app" / "current.txt").exists()


def test_onedir_migration_refuses_legacy_target_appearing_during_staging(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "onedir late legacy appearance"
    install_dir.mkdir()
    legacy_binary = install_dir / "opensre.exe"
    unrelated_bytes = b"late unrelated legacy target"
    unrelated_payload = base64.b64encode(unrelated_bytes).decode("ascii")
    replacement = _make_onedir_bundle(tmp_path / "onedir appearance replacement")
    override = f"""
$script:OpenSreOriginalCopyInstallTree = ${{function:Copy-OpenSreInstallTree}}
$script:OpenSreLegacyTargetCreated = $false
function Copy-OpenSreInstallTree {{
    param([string]$Source, [string]$Destination)
    & $script:OpenSreOriginalCopyInstallTree -Source $Source -Destination $Destination
    if (-not $script:OpenSreLegacyTargetCreated) {{
        $script:OpenSreLegacyTargetCreated = $true
        [System.IO.File]::WriteAllBytes(
            {_ps_literal(legacy_binary)},
            [System.Convert]::FromBase64String('{unrelated_payload}')
        )
    }}
}}
"""

    completed, result = _install_bundle(
        binary_path=replacement,
        install_dir=install_dir,
        install_id="must-not-retire-late-legacy",
        cwd=tmp_path,
        installer_override=override,
        check=False,
    )

    assert completed.returncode != 0
    assert result is None
    assert "Refusing to replace unverified pre-existing executable" in completed.stderr
    assert legacy_binary.read_bytes() == unrelated_bytes
    assert not (install_dir / "opensre.cmd").exists()
    assert not (install_dir / ".opensre-app" / "current.txt").exists()


def test_running_legacy_onefile_cleanup_waits_for_process_exit(tmp_path: Path) -> None:
    install_dir = tmp_path / "legacy install with spaces"
    install_dir.mkdir()
    legacy_binary = install_dir / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), legacy_binary)
    replacement_binary = _make_onedir_bundle(tmp_path / "replacement bundle")
    legacy_alias = _short_path(legacy_binary, cwd=tmp_path)
    assert str(legacy_alias).casefold() != str(legacy_binary).casefold()
    release = tmp_path / "release-legacy-process"
    worker_observed = tmp_path / "cleanup-worker-observed-parent"
    running_legacy = subprocess.Popen([str(legacy_alias), "hold-until", str(release)])

    try:
        parent_started = _process_started_token(running_legacy.pid, cwd=tmp_path)
        scoped_installer = _write_scoped_cleanup_installer(
            tmp_path,
            process_id=running_legacy.pid,
            process_started=parent_started,
            parent_observed=worker_observed,
        )
        completed, result = _install_bundle(
            binary_path=replacement_binary,
            install_dir=install_dir,
            install_id="deferred-migration",
            cwd=tmp_path,
            parent_process_id=running_legacy.pid,
            parent_executable_path=legacy_binary,
            parent_started=parent_started,
            verified_legacy_binary_path=legacy_binary,
            installer_path=scoped_installer,
        )
        assert result is not None
        assert result["DeferredCleanup"] is True
        assert not legacy_binary.exists()
        cleanup_path = _path(result, "CleanupPath")
        _wait_until(
            lambda: (
                worker_observed.is_file()
                or not cleanup_path.exists()
                or running_legacy.poll() is not None
            ),
            timeout=_DETACHED_CLEANUP_TIMEOUT,
        )
        assert running_legacy.poll() is None, "legacy process exited before worker observation"
        assert worker_observed.is_file(), (
            "cleanup worker exited before observing the running parent\n"
            + completed.stdout
            + completed.stderr
        )

        release.write_text("release", encoding="utf-8")
        running_legacy.wait(timeout=10)
        _wait_until(lambda: not cleanup_path.exists(), timeout=_DETACHED_CLEANUP_TIMEOUT)
        layout_root = install_dir / ".opensre-app"

        assert list(layout_root.glob("retired-*")) == []
        assert _probe_launcher(_path(result, "LauncherPath"), cwd=tmp_path)["VersionExit"] == 0
    finally:
        release.write_text("release", encoding="utf-8")
        if running_legacy.poll() is None:
            running_legacy.terminate()
            running_legacy.wait(timeout=10)


def test_running_legacy_onefile_without_parent_pid_defers_locked_cleanup(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "legacy install without pid"
    install_dir.mkdir()
    legacy_binary = install_dir / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), legacy_binary)
    replacement_binary = _make_onedir_bundle(tmp_path / "replacement without pid")
    release = tmp_path / "release-legacy-process"
    worker_ready = tmp_path / "cleanup-worker-saw-running-process"
    running_legacy = subprocess.Popen([str(legacy_binary), "hold-until", str(release)])

    try:
        process_started = _process_started_token(running_legacy.pid, cwd=tmp_path)
        scoped_installer = _write_scoped_cleanup_installer(
            tmp_path,
            process_id=running_legacy.pid,
            process_started=process_started,
            second_scan_ready=worker_ready,
        )
        completed, result = _install_bundle(
            binary_path=replacement_binary,
            install_dir=install_dir,
            install_id="deferred-without-pid",
            cwd=tmp_path,
            verified_legacy_binary_path=legacy_binary,
            installer_path=scoped_installer,
        )
        assert result is not None
        assert result["DeferredCleanup"] is True
        assert not legacy_binary.exists()
        assert running_legacy.poll() is None
        cleanup_path = _path(result, "CleanupPath")
        _wait_until(
            lambda: (
                worker_ready.is_file()
                or not cleanup_path.exists()
                or running_legacy.poll() is not None
            ),
            timeout=_DETACHED_CLEANUP_TIMEOUT,
        )
        assert running_legacy.poll() is None, "legacy process exited before worker observation"
        assert worker_ready.is_file(), (
            "cleanup worker exited before observing the running process\n"
            + completed.stdout
            + completed.stderr
        )

        release.write_text("release", encoding="utf-8")
        running_legacy.wait(timeout=15)
        _wait_until(lambda: not cleanup_path.exists(), timeout=_DETACHED_CLEANUP_TIMEOUT)
        layout_root = install_dir / ".opensre-app"

        assert list(layout_root.glob("retired-*")) == []
        launcher = _path(result, "LauncherPath")
        assert _probe_launcher(launcher, cwd=tmp_path)["VersionExit"] == 0
    finally:
        release.write_text("release", encoding="utf-8")
        if running_legacy.poll() is None:
            running_legacy.terminate()
            running_legacy.wait(timeout=10)


def test_launcher_forwards_arguments_and_exit_code_from_paths_with_spaces(
    tmp_path: Path,
) -> None:
    binary = _make_onedir_bundle(tmp_path / "source bundle with spaces")
    install_dir = tmp_path / "installed application with spaces"

    _, result = _install_bundle(
        binary_path=binary,
        install_dir=install_dir,
        install_id="space-safe-build",
        cwd=tmp_path,
    )
    assert result is not None

    probe = _probe_launcher(_path(result, "LauncherPath"), cwd=tmp_path)

    assert probe["ArgumentExit"] == 0
    assert "value with spaces" in probe["ArgumentOutput"]
    assert probe["ForwardedExit"] == 37


def test_launcher_rejects_traversal_and_metacharacter_current_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = _make_onedir_bundle(tmp_path / "launcher validation source")
    install_dir = tmp_path / "launcher validation install"
    _, result = _install_bundle(
        binary_path=binary,
        install_dir=install_dir,
        install_id="safe-build",
        cwd=tmp_path,
    )
    assert result is not None
    launcher = _path(result, "LauncherPath")
    pointer = install_dir / ".opensre-app" / "current.txt"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_binary = outside / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), outside_binary)
    execution_marker = tmp_path / "outside-executed.txt"
    injection_marker = tmp_path / "injected.txt"
    monkeypatch.setenv("OPENSRE_TEST_GUARDED_EXECUTABLE", str(outside_binary))
    monkeypatch.setenv("OPENSRE_TEST_EXECUTION_MARKER", str(execution_marker))

    for invalid_value in (
        r"..\..\..\outside",
        "safe&echo injected>injected.txt&rem",
        ".",
        "..",
        ".hidden",
        "trailing.",
        "-leading",
        "trailing-",
    ):
        pointer.write_text(f"{invalid_value}\n", encoding="utf-8")
        completed = _run_powershell(
            f"& {_ps_literal(launcher)} --version; exit $LASTEXITCODE",
            cwd=tmp_path,
        )
        assert completed.returncode != 0
        assert "invalid current version pointer" in completed.stderr

    assert not execution_marker.exists()
    assert not injection_marker.exists()


def test_installer_updates_onedir_files_beyond_max_path(tmp_path: Path) -> None:
    binary = _make_onedir_bundle(
        tmp_path / "long destination source",
        payload={"base.txt": "base payload"},
    )
    deep_relative = Path("_internal")
    for index in range(5):
        deep_relative /= f"segment-{index}-" + ("x" * 28)
    deep_relative /= "payload.txt"
    source_payload = binary.parent / deep_relative
    extended_source_parent = "\\\\?\\" + str(source_payload.parent)
    created = _run_powershell(
        f"""
$directory = [System.IO.Directory]::CreateDirectory({_ps_literal(extended_source_parent)})
[System.IO.File]::WriteAllText(
    [System.IO.Path]::Combine($directory.FullName, 'payload.txt'),
    'long path payload'
)
""",
        cwd=tmp_path,
    )
    assert created.returncode == 0, created.stdout + created.stderr

    install_id = "long-path-build"
    install_dir = tmp_path / "long destination with spaces"
    expected_payload = install_dir / ".opensre-app" / "versions" / install_id / deep_relative
    assert len(str(expected_payload)) > 260

    _, first = _install_bundle(
        binary_path=binary,
        install_dir=install_dir,
        install_id=install_id,
        cwd=tmp_path,
    )

    assert first is not None
    extended_payload = Path("\\\\?\\" + str(expected_payload))
    assert extended_payload.read_text(encoding="utf-8") == "long path payload"

    replacement_id = "long-path-replacement"
    replacement = _make_onedir_bundle(
        tmp_path / "long destination replacement",
        payload={"base.txt": "replacement base payload"},
    )
    replacement_source_payload = replacement.parent / deep_relative
    replacement_created = _run_powershell(
        f"""
$directory = [System.IO.Directory]::CreateDirectory({_ps_literal("\\\\?\\" + str(replacement_source_payload.parent))})
[System.IO.File]::WriteAllText(
    [System.IO.Path]::Combine($directory.FullName, 'payload.txt'),
    'updated long path payload'
)
""",
        cwd=tmp_path,
    )
    assert replacement_created.returncode == 0, (
        replacement_created.stdout + replacement_created.stderr
    )
    replacement_payload = install_dir / ".opensre-app" / "versions" / replacement_id / deep_relative
    _, second = _install_bundle(
        binary_path=replacement,
        install_dir=install_dir,
        install_id=replacement_id,
        cwd=tmp_path,
    )

    assert second is not None
    assert Path("\\\\?\\" + str(replacement_payload)).read_text(encoding="utf-8") == (
        "updated long path payload"
    )
    assert _probe_launcher(_path(second, "LauncherPath"), cwd=tmp_path)["VersionExit"] == 0


def test_installer_rejects_unusable_long_command_path_before_mutation(tmp_path: Path) -> None:
    binary = _make_onedir_bundle(tmp_path / "long command source")
    install_dir = tmp_path / "long command destination"
    while len(str(install_dir)) <= 270:
        install_dir /= "segment-" + ("x" * 24)
    sentinel = install_dir / "keep.txt"
    extended_install_dir = "\\\\?\\" + str(install_dir)
    extended_sentinel = "\\\\?\\" + str(sentinel)
    created = _run_powershell(
        f"""
[System.IO.Directory]::CreateDirectory({_ps_literal(extended_install_dir)}) | Out-Null
[System.IO.File]::WriteAllText({_ps_literal(extended_sentinel)}, 'preserve')
""",
        cwd=tmp_path,
    )
    assert created.returncode == 0, created.stdout + created.stderr

    completed, result = _install_bundle(
        binary_path=binary,
        install_dir=install_dir,
        install_id="unusable-command-path",
        cwd=tmp_path,
        check=False,
    )

    assert result is None
    assert completed.returncode != 0
    assert "stable Windows command path must be shorter than 260 characters" in completed.stderr
    verification = _run_powershell(
        f"""
$payload = [ordered]@{{
    Sentinel = [System.IO.File]::ReadAllText({_ps_literal(extended_sentinel)})
    LayoutExists = [System.IO.Directory]::Exists(
        {_ps_literal("\\\\?\\" + str(install_dir / ".opensre-app"))}
    )
    LockExists = [System.IO.File]::Exists(
        {_ps_literal("\\\\?\\" + str(install_dir / ".opensre-app.install.lock"))}
    )
}}
Write-Output ({_ps_literal(_CONTEXT_PREFIX)} + ($payload | ConvertTo-Json -Compress))
""",
        cwd=tmp_path,
    )
    assert verification.returncode == 0, verification.stdout + verification.stderr
    preserved = _prefixed_json(verification.stdout, _CONTEXT_PREFIX)
    assert preserved == {"Sentinel": "preserve", "LayoutExists": False, "LockExists": False}


def test_stage_to_final_move_retries_transient_access_denied(tmp_path: Path) -> None:
    install_id = "transient-stage-move"
    install_dir = tmp_path / "transient stage move install"
    binary = _make_onedir_bundle(
        tmp_path / "transient stage move bundle",
        payload={"nested/payload.dat": "complete after retry"},
    )
    layout_root = install_dir / ".opensre-app"
    stage_path = layout_root / f"stage-{install_id}"
    final_path = layout_root / "versions" / install_id
    attempt_marker = tmp_path / "stage-move-attempts.txt"
    installer_override = rf"""
$script:OpenSreInjectedMoveAttempts = 0
function Move-OpenSreInstallDirectory {{
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )
    if ([System.IO.Path]::GetFullPath($Source) -eq {_ps_literal(stage_path)} -and
        [System.IO.Path]::GetFullPath($Destination) -eq {_ps_literal(final_path)}) {{
        $script:OpenSreInjectedMoveAttempts += 1
        [System.IO.File]::WriteAllText(
            {_ps_literal(attempt_marker)},
            [string]$script:OpenSreInjectedMoveAttempts
        )
        if ($script:OpenSreInjectedMoveAttempts -eq 1) {{
            throw [System.UnauthorizedAccessException]::new('injected transient access denied')
        }}
    }}
    [System.IO.Directory]::Move(
        (ConvertTo-OpenSreExtendedPath -Path $Source),
        (ConvertTo-OpenSreExtendedPath -Path $Destination)
    )
}}
"""

    _, result = _install_bundle(
        binary_path=binary,
        install_dir=install_dir,
        install_id=install_id,
        cwd=tmp_path,
        installer_override=installer_override,
    )

    assert result is not None
    assert attempt_marker.read_text(encoding="utf-8") == "2"
    assert not stage_path.exists()
    assert final_path.is_dir()
    assert (final_path / "_internal" / "nested" / "payload.dat").read_text(
        encoding="utf-8"
    ) == "complete after retry"
    assert _probe_launcher(_path(result, "LauncherPath"), cwd=tmp_path)["VersionExit"] == 0


def test_marker_owned_launcher_is_atomically_rewritten_to_canonical_content(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "owned launcher install"
    first_binary = _make_onedir_bundle(tmp_path / "owned launcher first")
    _, first = _install_bundle(
        binary_path=first_binary,
        install_dir=install_dir,
        install_id="owned-first",
        cwd=tmp_path,
    )
    assert first is not None
    launcher = _path(first, "LauncherPath")
    canonical_content = launcher.read_bytes()
    tamper_marker = tmp_path / "tampered-launcher-ran.txt"
    launcher.write_bytes(
        "\r\n".join(
            (
                "@echo off",
                ":: OpenSRE Windows launcher v1",
                f'echo tampered>"{tamper_marker}"',
                f'"{_path(first, "BinaryPath")}" %*',
                "exit /b %ERRORLEVEL%",
                "",
            )
        ).encode("utf-8")
    )

    second_binary = _make_onedir_bundle(tmp_path / "owned launcher second")
    _, second = _install_bundle(
        binary_path=second_binary,
        install_dir=install_dir,
        install_id="owned-second",
        cwd=tmp_path,
    )

    assert second is not None
    assert launcher.read_bytes() == canonical_content
    assert not tamper_marker.exists()
    pointer = install_dir / ".opensre-app" / "current.txt"
    assert pointer.read_text(encoding="utf-8").strip() == "owned-second"
    assert _probe_launcher(launcher, cwd=tmp_path)["VersionOutput"].startswith("opensre, version ")


def test_installer_refuses_launcher_swapped_after_ownership_check(tmp_path: Path) -> None:
    install_dir = tmp_path / "launcher post-ownership swap"
    first_binary = _make_onedir_bundle(tmp_path / "launcher swap first")
    _, first = _install_bundle(
        binary_path=first_binary,
        install_dir=install_dir,
        install_id="launcher-swap-stable",
        cwd=tmp_path,
    )
    assert first is not None
    launcher = _path(first, "LauncherPath")
    approved_hash = _sha256(launcher)
    preserved_approved = install_dir / "approved-opensre.cmd"
    unrelated_bytes = b"@echo off\r\necho unrelated user launcher\r\n"
    unrelated_payload = base64.b64encode(unrelated_bytes).decode("ascii")
    replacement = _make_onedir_bundle(tmp_path / "launcher swap replacement")
    override = f"""
$script:OpenSreOriginalTestInstallFileSnapshot = ${{function:Test-OpenSreInstallFileSnapshot}}
$script:OpenSreLauncherSwapped = $false
function Test-OpenSreInstallFileSnapshot {{
    param([string]$Path, [object]$Expected, [switch]$AllowRelocated)
    $result = & $script:OpenSreOriginalTestInstallFileSnapshot `
        -Path $Path `
        -Expected $Expected `
        -AllowRelocated:$AllowRelocated
    if ($result -and -not $AllowRelocated -and
        -not $script:OpenSreLauncherSwapped -and
        (Test-OpenSreSamePath -Left $Path -Right {_ps_literal(launcher)})) {{
        $script:OpenSreLauncherSwapped = $true
        [System.IO.File]::Move(
            {_ps_literal(launcher)},
            {_ps_literal(preserved_approved)}
        )
        [System.IO.File]::WriteAllBytes(
            {_ps_literal(launcher)},
            [System.Convert]::FromBase64String('{unrelated_payload}')
        )
    }}
    return $result
}}
"""

    completed, result = _install_bundle(
        binary_path=replacement,
        install_dir=install_dir,
        install_id="launcher-swap-refused",
        cwd=tmp_path,
        installer_override=override,
        check=False,
    )

    assert completed.returncode != 0
    assert result is None
    assert "Refusing to replace a changed launcher" in completed.stderr
    assert launcher.read_bytes() == unrelated_bytes
    assert _sha256(preserved_approved) == approved_hash
    pointer = install_dir / ".opensre-app" / "current.txt"
    assert pointer.read_text(encoding="utf-8").strip() == "launcher-swap-stable"
    assert _path(first, "BinaryPath").is_file()
    assert not (install_dir / ".opensre-app" / "versions" / "launcher-swap-refused").exists()


def test_installer_refuses_genuinely_unowned_launcher(tmp_path: Path) -> None:
    install_dir = tmp_path / "unowned launcher install"
    install_dir.mkdir()
    launcher = install_dir / "opensre.cmd"
    original_content = b"@echo off\r\necho user-owned\r\n"
    launcher.write_bytes(original_content)
    binary = _make_onedir_bundle(tmp_path / "unowned launcher bundle")

    completed, result = _install_bundle(
        binary_path=binary,
        install_dir=install_dir,
        install_id="must-not-activate",
        cwd=tmp_path,
        check=False,
    )

    assert completed.returncode != 0
    assert result is None
    assert "Refusing to replace unowned launcher" in completed.stderr
    assert launcher.read_bytes() == original_content
    assert not (install_dir / ".opensre-app" / "current.txt").exists()


@pytest.mark.parametrize(
    ("body", "expected_error"),
    (
        ("@echo off\r\nexit /b 0\r\n", "empty output"),
        ("@echo off\r\necho another-tool 9.9\r\nexit /b 0\r\n", "valid OpenSRE"),
        (
            "@echo off\r\necho opensre, version 0.1\r\necho unexpected extra output\r\nexit /b 0\r\n",
            "valid OpenSRE",
        ),
    ),
)
def test_version_validation_rejects_empty_or_non_opensre_output(
    tmp_path: Path,
    body: str,
    expected_error: str,
) -> None:
    invalid_launcher = tmp_path / "invalid-version.cmd"
    invalid_launcher.write_text(body, encoding="utf-8")
    completed = _run_powershell(
        f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(INSTALL_PS1)} -SkipMain
Get-OpenSreBinaryVersionInfo -BinaryPath {_ps_literal(invalid_launcher)}
""",
        cwd=tmp_path,
    )

    assert completed.returncode != 0
    assert expected_error in completed.stderr


def test_cleanup_launch_failure_after_activation_keeps_new_bundle_working(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "cleanup launch failure"
    first_binary = _make_onedir_bundle(tmp_path / "cleanup failure first")
    _, first = _install_bundle(
        binary_path=first_binary,
        install_dir=install_dir,
        install_id="cleanup-stable",
        cwd=tmp_path,
    )
    assert first is not None
    first_root = _path(first, "AppRoot")
    replacement = _make_onedir_bundle(tmp_path / "cleanup failure replacement")

    completed, second = _install_bundle(
        binary_path=replacement,
        install_dir=install_dir,
        install_id="cleanup-activated",
        cwd=tmp_path,
        installer_override="""
function Start-OpenSreDeferredCleanup {
    param([string]$LayoutRoot, [string[]]$TargetPaths, [int]$ParentProcessId)
    throw 'forced cleanup launch failure'
}
""",
    )

    assert second is not None
    second_root = _path(second, "AppRoot")
    pointer = install_dir / ".opensre-app" / "current.txt"
    assert pointer.read_text(encoding="utf-8").strip() == "cleanup-activated"
    assert first_root.is_dir()
    assert second_root.is_dir()
    assert "retained for a later safe cleanup" in completed.stdout
    assert _probe_launcher(_path(second, "LauncherPath"), cwd=tmp_path)["VersionExit"] == 0


def test_obsolete_enumeration_failure_after_activation_keeps_new_bundle_working(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "obsolete enumeration failure"
    first_binary = _make_onedir_bundle(tmp_path / "obsolete enumeration first")
    _, first = _install_bundle(
        binary_path=first_binary,
        install_dir=install_dir,
        install_id="enumeration-stable",
        cwd=tmp_path,
    )
    assert first is not None
    first_root = _path(first, "AppRoot")
    first_manifest = {
        path.relative_to(first_root): _sha256(path)
        for path in first_root.rglob("*")
        if path.is_file()
    }
    replacement = _make_onedir_bundle(tmp_path / "obsolete enumeration replacement")

    completed, second = _install_bundle(
        binary_path=replacement,
        install_dir=install_dir,
        install_id="enumeration-activated",
        cwd=tmp_path,
        installer_override="""
function Get-OpenSreObsoleteVersionPaths {
    param([string]$LayoutRoot, [string]$ActiveInstallId)
    throw 'forced obsolete-version enumeration failure'
}
""",
    )

    assert second is not None
    second_root = _path(second, "AppRoot")
    pointer = install_dir / ".opensre-app" / "current.txt"
    assert pointer.read_text(encoding="utf-8").strip() == "enumeration-activated"
    assert first_root.is_dir()
    assert {
        path.relative_to(first_root): _sha256(path)
        for path in first_root.rglob("*")
        if path.is_file()
    } == first_manifest
    assert second_root.is_dir()
    assert second["DeferredCleanup"] is True
    assert "obsolete Windows files could not be enumerated" in completed.stdout
    assert _probe_launcher(_path(second, "LauncherPath"), cwd=tmp_path)["VersionExit"] == 0


def test_legacy_cleanup_launch_failure_is_retried_by_a_later_install(tmp_path: Path) -> None:
    install_dir = tmp_path / "legacy cleanup retry"
    install_dir.mkdir()
    legacy_binary = install_dir / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), legacy_binary)
    legacy_hash = _sha256(legacy_binary)
    replacement = _make_onedir_bundle(tmp_path / "legacy cleanup replacement")

    failed_cleanup, migrated = _install_bundle(
        binary_path=replacement,
        install_dir=install_dir,
        install_id="legacy-cleanup-retained",
        cwd=tmp_path,
        verified_legacy_binary_path=legacy_binary,
        installer_override="""
function Start-OpenSreDeferredCleanup {
    param([string]$LayoutRoot, [string[]]$TargetPaths, [int]$ParentProcessId)
    throw 'forced cleanup launch failure'
}
""",
    )

    assert migrated is not None
    layout_root = install_dir / ".opensre-app"
    retained = list(layout_root.glob("retired-*"))
    assert len(retained) == 1
    assert _sha256(retained[0]) == legacy_hash
    assert "retained for a later safe cleanup" in failed_cleanup.stdout

    next_binary = _make_onedir_bundle(tmp_path / "legacy cleanup retry bundle")
    _, retried = _install_bundle(
        binary_path=next_binary,
        install_dir=install_dir,
        install_id="legacy-cleanup-retried",
        cwd=tmp_path,
    )

    assert retried is not None
    _wait_until(lambda: not retained[0].exists())
    assert _probe_launcher(_path(retried, "LauncherPath"), cwd=tmp_path)["VersionExit"] == 0


def test_update_retains_complete_old_bundle_used_by_second_process(tmp_path: Path) -> None:
    install_dir = tmp_path / "two process update"
    first_binary = _make_onedir_bundle(
        tmp_path / "two process first",
        payload={"lazy/module.dat": "must remain complete", "shared.dat": "old"},
    )
    _, first = _install_bundle(
        binary_path=first_binary,
        install_dir=install_dir,
        install_id="two-process-old",
        cwd=tmp_path,
    )
    assert first is not None
    old_root = _path(first, "AppRoot")
    old_manifest = {
        path.relative_to(old_root): _sha256(path) for path in old_root.rglob("*") if path.is_file()
    }
    release_parent = tmp_path / "release-update-parent"
    release_busy = tmp_path / "release-busy-process"
    short_process = subprocess.Popen(
        [str(old_root / "opensre.exe"), "hold-until", str(release_parent)]
    )
    long_process = subprocess.Popen(
        [str(old_root / "opensre.exe"), "hold-until", str(release_busy)]
    )

    try:
        second_binary = _make_onedir_bundle(tmp_path / "two process second")
        _, second = _install_bundle(
            binary_path=second_binary,
            install_dir=install_dir,
            install_id="two-process-new",
            cwd=tmp_path,
            parent_process_id=short_process.pid,
        )
        assert second is not None
        release_parent.write_text("release", encoding="utf-8")
        short_process.wait(timeout=10)
        cleanup_path = _path(second, "CleanupPath")
        assert cleanup_path.is_file()
        _wait_until(lambda: not cleanup_path.exists())

        assert long_process.poll() is None
        assert old_root.is_dir()
        assert {
            path.relative_to(old_root): _sha256(path)
            for path in old_root.rglob("*")
            if path.is_file()
        } == old_manifest
        assert (old_root / "_internal" / "lazy" / "module.dat").read_text(
            encoding="utf-8"
        ) == "must remain complete"
        assert _probe_launcher(_path(second, "LauncherPath"), cwd=tmp_path)["VersionExit"] == 0

        release_busy.write_text("release", encoding="utf-8")
        long_process.wait(timeout=10)
        third_binary = _make_onedir_bundle(tmp_path / "two process third")
        third_completed, third = _install_bundle(
            binary_path=third_binary,
            install_dir=install_dir,
            install_id="two-process-cleanup-retry",
            cwd=tmp_path,
        )
        assert third is not None
        assert third["DeferredCleanup"] is True
        _wait_until(lambda: not old_root.exists())
        assert not old_root.exists(), third_completed.stdout + third_completed.stderr
    finally:
        release_parent.write_text("release", encoding="utf-8")
        release_busy.write_text("release", encoding="utf-8")
        for process in (short_process, long_process):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)


@pytest.mark.parametrize(
    "process_override",
    (_FAILED_PROCESS_ENUMERATOR, _PARTIAL_PROCESS_ENUMERATOR),
    ids=("enumeration", "partial-process-path"),
)
def test_update_cleanup_worker_retains_old_tree_when_process_scan_fails(
    tmp_path: Path,
    process_override: str,
) -> None:
    install_dir = tmp_path / "process scan failure update"
    first_binary = _make_onedir_bundle(
        tmp_path / "process scan failure first",
        payload={"lazy/module.dat": "must remain complete", "shared.dat": "old"},
    )
    _, first = _install_bundle(
        binary_path=first_binary,
        install_dir=install_dir,
        install_id="scan-failure-old",
        cwd=tmp_path,
    )
    assert first is not None
    old_root = _path(first, "AppRoot")
    old_manifest = {
        path.relative_to(old_root): _sha256(path) for path in old_root.rglob("*") if path.is_file()
    }

    installer_source = INSTALL_PS1.read_text(encoding="utf-8")
    faulted_installer = tmp_path / "install-process-scan-failure.ps1"
    faulted_source = _inject_failed_process_enumerator(
        installer_source,
        preference='"SilentlyContinue"',
    ).replace(_FAILED_PROCESS_ENUMERATOR, process_override, 1)
    faulted_installer.write_text(
        _set_embedded_cleanup_lock_timeout(faulted_source, seconds=3),
        encoding="utf-8",
    )

    replacement = _make_onedir_bundle(tmp_path / "process scan failure replacement")
    _, second = _install_bundle(
        binary_path=replacement,
        install_dir=install_dir,
        install_id="scan-failure-new",
        cwd=tmp_path,
        installer_path=faulted_installer,
    )
    assert second is not None
    layout_root = install_dir / ".opensre-app"
    cleanup_path = _path(second, "CleanupPath")
    assert cleanup_path.is_file()
    _wait_until(lambda: not cleanup_path.exists())

    assert (layout_root / "current.txt").read_text(encoding="utf-8").strip() == ("scan-failure-new")
    assert old_root.is_dir()
    assert {
        path.relative_to(old_root): _sha256(path) for path in old_root.rglob("*") if path.is_file()
    } == old_manifest
    assert list(layout_root.glob("retired-*")) == []
    assert _probe_launcher(_path(second, "LauncherPath"), cwd=tmp_path)["VersionExit"] == 0


def test_onedir_archive_installs_from_a_literal_extraction_path(tmp_path: Path) -> None:
    source_binary = _make_onedir_bundle(
        tmp_path / "archive source",
        payload={"nested/payload.dat": "complete archive"},
    )
    source_root = source_binary.parent
    archive = tmp_path / "opensre_main_windows-x64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle_zip:
        for path in source_root.rglob("*"):
            if path.is_file():
                bundle_zip.write(path, arcname=Path("opensre") / path.relative_to(source_root))
    extraction_root = tmp_path / "extraction [literal] path"
    completed = _run_powershell(
        f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(INSTALL_PS1)} -SkipMain
Expand-Archive -LiteralPath {_ps_literal(archive)} -DestinationPath {_ps_literal(extraction_root)}
$binary = Get-OpenSreBinaryPathFromArchive `
    -ExtractionRoot {_ps_literal(extraction_root)} `
    -BinaryName 'opensre.exe'
Write-Output $binary
""",
        cwd=tmp_path,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    extracted_binary = extraction_root / "opensre" / "opensre.exe"
    assert str(extracted_binary) in completed.stdout

    install_dir = tmp_path / "archive install [literal] path"
    _, result = _install_bundle(
        binary_path=extracted_binary,
        install_dir=install_dir,
        install_id="archive-literal-path",
        cwd=tmp_path,
    )
    assert result is not None
    shutil.rmtree(extraction_root)

    app_root = _path(result, "AppRoot")
    assert (app_root / "_internal" / "nested" / "payload.dat").read_text(
        encoding="utf-8"
    ) == "complete archive"
    assert _probe_launcher(_path(result, "LauncherPath"), cwd=tmp_path)["VersionExit"] == 0


def _parent_context_override(executable: Path, *, pid: int, started: str) -> str:
    return f"""
function Get-OpenSreImmediateParentContext {{
    return [pscustomobject]@{{
        ProcessId = {pid}
        ExecutablePath = {_ps_literal(executable)}
        Started = {_ps_literal(started)}
    }}
}}
"""


def test_update_context_uses_custom_managed_install_directory(tmp_path: Path) -> None:
    install_dir = tmp_path / "custom managed install"
    app_root = install_dir / ".opensre-app"
    version_root = app_root / "versions" / "build-one"
    version_root.mkdir(parents=True)
    executable = version_root / "opensre.exe"
    executable.write_bytes(b"MZ")
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (install_dir / "opensre.cmd").write_text(
        "@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8"
    )

    _, context = _resolve_install_context(
        cwd=tmp_path,
        update_executable=executable,
        parent_process_id=5844,
        parent_started="managed-start",
        installer_override=_parent_context_override(executable, pid=5844, started="managed-start"),
    )

    assert context is not None
    assert Path(context["InstallDir"]) == install_dir
    assert context["ParentProcessId"] == 5844
    assert context["IsUpdate"] is True


def test_canonical_identity_treats_83_alias_as_the_same_path(tmp_path: Path) -> None:
    installer_alias = _short_path(INSTALL_PS1, cwd=tmp_path)
    if str(installer_alias).casefold() == str(INSTALL_PS1).casefold():
        pytest.skip("8.3 aliases are disabled on the test volume")

    completed = _run_powershell(
        f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(INSTALL_PS1)} -SkipMain
if (-not (Test-OpenSreSamePath `
        -Left {_ps_literal(INSTALL_PS1)} `
        -Right {_ps_literal(installer_alias)})) {{
    $leftPath = Get-OpenSreCanonicalPath -Path {_ps_literal(INSTALL_PS1)}
    $rightPath = Get-OpenSreCanonicalPath -Path {_ps_literal(installer_alias)}
    throw "8.3 alias did not resolve to the same path: '$leftPath' <> '$rightPath'"
}}
""",
        cwd=tmp_path,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_update_context_uses_custom_legacy_onefile_directory(tmp_path: Path) -> None:
    install_dir = tmp_path / "custom legacy install"
    install_dir.mkdir()
    executable = install_dir / "opensre.exe"
    executable.write_bytes(b"MZ")

    _, context = _resolve_install_context(
        cwd=tmp_path,
        update_executable=executable,
        parent_process_id=731,
        parent_started="legacy-start",
        installer_override=_parent_context_override(executable, pid=731, started="legacy-start"),
    )

    assert context is not None
    assert Path(context["InstallDir"]) == install_dir
    assert context["ParentProcessId"] == 731
    assert Path(context["LegacyBinaryPath"]) == executable


def test_update_context_does_not_trust_spoofed_nonparent_legacy_process(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "spoofed legacy context"
    install_dir.mkdir()
    executable = install_dir / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), executable)
    release = tmp_path / "release-unrelated-process"
    unrelated_process = subprocess.Popen([str(executable), "hold-until", str(release)])

    try:
        completed, context = _resolve_install_context(
            cwd=tmp_path,
            update_executable=executable,
            parent_process_id=unrelated_process.pid,
            parent_started="spoofed-start",
            check=False,
        )

        assert completed.returncode != 0
        assert context is None
        assert "unverified OpenSRE update process handoff" in completed.stderr
    finally:
        unrelated_process.terminate()
        unrelated_process.wait(timeout=10)


def test_update_context_rejects_reused_pid_creation_token(tmp_path: Path) -> None:
    executable = _make_onedir_bundle(tmp_path / "pid reuse bundle")
    completed, context = _resolve_install_context(
        cwd=tmp_path,
        update_executable=executable,
        parent_process_id=5935,
        parent_started="stale-process-start",
        installer_override=_parent_context_override(
            executable, pid=5935, started="replacement-process-start"
        ),
        check=False,
    )

    assert completed.returncode != 0
    assert context is None
    assert "unverified OpenSRE update process handoff" in completed.stderr


def test_explicit_install_directory_is_used_without_update_handoff(tmp_path: Path) -> None:
    explicit_dir = tmp_path / "explicit install"

    _, context = _resolve_install_context(
        cwd=tmp_path,
        explicit_install_dir=explicit_dir,
    )

    assert context is not None
    assert Path(context["InstallDir"]) == explicit_dir
    assert context["ParentProcessId"] == 0


@pytest.mark.parametrize("preexisting_lock", (False, True))
def test_install_lock_binds_validation_and_cleanup_to_opened_file_identity(
    tmp_path: Path,
    preexisting_lock: bool,
) -> None:
    install_dir = tmp_path / "lock identity install"
    install_dir.mkdir()
    preserved_install = tmp_path / "preserved lock identity install"
    outside = tmp_path / "outside lock identity"
    outside.mkdir()
    outside_lock = outside / ".opensre-app.install.lock"
    if preexisting_lock:
        outside_lock.write_text("preserve existing lock", encoding="utf-8")

    try:
        completed = _run_powershell(
            f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(INSTALL_PS1)} -SkipMain
$null = Initialize-OpenSreInstallLockNativeApi
$script:OpenSreLockPathSwapped = $false
function New-OpenSreNativeInstallLock {{
    param([string]$Path, [switch]$CreateNew)
    if (-not $script:OpenSreLockPathSwapped) {{
        [System.IO.Directory]::Move(
            {_ps_literal(install_dir)},
            {_ps_literal(preserved_install)}
        )
        New-Item `
            -ItemType Junction `
            -Path {_ps_literal(install_dir)} `
            -Target {_ps_literal(outside)} | Out-Null
        $script:OpenSreLockPathSwapped = $true
    }}
    return [OpenSre.InstallLockNativeApiV1]::OpenInstallLock(
        (ConvertTo-OpenSreExtendedPath -Path $Path),
        [bool]$CreateNew
    )
}}
$failure = ''
try {{
    $lock = Open-OpenSreInstallLock -InstallDir {_ps_literal(install_dir)}
    $lock.Dispose()
}}
catch {{
    $failure = [string]$_.Exception.Message
}}
if (-not $failure) {{
    throw 'The identity-swapped install lock was accepted.'
}}
Write-Output $failure
""",
            cwd=tmp_path,
        )

        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "opened identity changed" in completed.stdout
        assert not (preserved_install / ".opensre-app.install.lock").exists()
        if preexisting_lock:
            assert outside_lock.read_text(encoding="utf-8") == "preserve existing lock"
        else:
            assert not outside_lock.exists()
    finally:
        if install_dir.is_junction():
            os.rmdir(install_dir)
        if preserved_install.exists() and not install_dir.exists():
            preserved_install.rename(install_dir)


def test_install_lock_rechecks_lexical_path_after_ancestor_swap_back(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "swap back lock identity install"
    install_dir.mkdir()
    preserved_install = tmp_path / "preserved swap back install"
    outside = tmp_path / "outside swap back lock identity"
    outside.mkdir()

    try:
        completed = _run_powershell(
            f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(INSTALL_PS1)} -SkipMain
$null = Initialize-OpenSreInstallLockNativeApi
$script:OriginalInstallPathAssert = ${{function:Assert-OpenSreInstallPathSafe}}
$script:OpenSreLockPathSwapped = $false
function Assert-OpenSreInstallPathSafe {{
    param([string]$InstallDir, [string]$Path, [string]$Purpose = 'OpenSRE installation path')
    & $script:OriginalInstallPathAssert `
        -InstallDir $InstallDir `
        -Path $Path `
        -Purpose $Purpose
    if (-not $script:OpenSreLockPathSwapped -and
        $Purpose -eq 'OpenSRE installation lock') {{
        [System.IO.Directory]::Move(
            {_ps_literal(install_dir)},
            {_ps_literal(preserved_install)}
        )
        New-Item `
            -ItemType Junction `
            -Path {_ps_literal(install_dir)} `
            -Target {_ps_literal(outside)} | Out-Null
        $script:OpenSreLockPathSwapped = $true
    }}
}}
function New-OpenSreNativeInstallLock {{
    param([string]$Path, [switch]$CreateNew)
    $lock = [OpenSre.InstallLockNativeApiV1]::OpenInstallLock(
        (ConvertTo-OpenSreExtendedPath -Path $Path),
        [bool]$CreateNew
    )
    [System.IO.Directory]::Delete({_ps_literal(install_dir)})
    [System.IO.Directory]::Move(
        {_ps_literal(preserved_install)},
        {_ps_literal(install_dir)}
    )
    return $lock
}}
$failure = ''
try {{
    $lock = Open-OpenSreInstallLock -InstallDir {_ps_literal(install_dir)}
    $lock.Dispose()
}}
catch {{
    $failure = [string]$_.Exception.Message
}}
if (-not $failure) {{
    throw 'The swap-back install lock was accepted.'
}}
Write-Output $failure
""",
            cwd=tmp_path,
        )

        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "opened identity" in completed.stdout
        assert not (install_dir / ".opensre-app.install.lock").exists()
        assert not (outside / ".opensre-app.install.lock").exists()
    finally:
        if install_dir.is_junction():
            os.rmdir(install_dir)
        if preserved_install.exists() and not install_dir.exists():
            preserved_install.rename(install_dir)


def test_install_lock_rejects_persistent_redirection_after_initial_path_check(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "persistent redirected lock install"
    install_dir.mkdir()
    preserved_install = tmp_path / "preserved persistent lock install"
    outside = tmp_path / "outside persistent lock install"
    outside.mkdir()

    try:
        completed = _run_powershell(
            f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(INSTALL_PS1)} -SkipMain
$script:OriginalInstallPathAssert = ${{function:Assert-OpenSreInstallPathSafe}}
$script:OpenSreLockPathSwapped = $false
function Assert-OpenSreInstallPathSafe {{
    param([string]$InstallDir, [string]$Path, [string]$Purpose = 'OpenSRE installation path')
    & $script:OriginalInstallPathAssert `
        -InstallDir $InstallDir `
        -Path $Path `
        -Purpose $Purpose
    if (-not $script:OpenSreLockPathSwapped -and
        $Purpose -eq 'OpenSRE installation lock') {{
        [System.IO.Directory]::Move(
            {_ps_literal(install_dir)},
            {_ps_literal(preserved_install)}
        )
        New-Item `
            -ItemType Junction `
            -Path {_ps_literal(install_dir)} `
            -Target {_ps_literal(outside)} | Out-Null
        $script:OpenSreLockPathSwapped = $true
    }}
}}
$failure = ''
try {{
    $lock = Open-OpenSreInstallLock -InstallDir {_ps_literal(install_dir)}
    $lock.Dispose()
}}
catch {{
    $failure = [string]$_.Exception.Message
}}
if (-not $failure) {{
    throw 'The persistently redirected install lock was accepted.'
}}
Write-Output $failure
""",
            cwd=tmp_path,
        )

        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "opened identity" in completed.stdout
        assert not (outside / ".opensre-app.install.lock").exists()
        assert not (preserved_install / ".opensre-app.install.lock").exists()
    finally:
        if install_dir.is_junction():
            os.rmdir(install_dir)
        if preserved_install.exists() and not install_dir.exists():
            preserved_install.rename(install_dir)


def test_install_lock_loads_after_legacy_native_path_api_is_already_loaded(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "same session legacy native api"
    install_dir.mkdir()

    completed = _run_powershell(
        f"""
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
namespace OpenSre
{{
    public static class NativePathApi
    {{
        public static string GetFinalPath(string path)
        {{
            return path;
        }}
    }}
}}
'@
. {_ps_literal(INSTALL_PS1)} -SkipMain
if ([OpenSre.NativePathApi].GetMethod('OpenInstallLock')) {{
    throw 'The legacy path API unexpectedly contains the new lock method.'
}}
$lock = Open-OpenSreInstallLock -InstallDir {_ps_literal(install_dir)}
try {{
    if (-not [bool]$lock.Created) {{
        throw 'The isolated native lock API did not create the install lock.'
    }}
    if ($lock.GetType().FullName -cne 'OpenSre.InstallLockLeaseV1') {{
        throw "Unexpected install lock type '$($lock.GetType().FullName)'."
    }}
    $lock.DeleteFileOnDispose()
}}
finally {{
    $lock.Dispose()
}}
""",
        cwd=tmp_path,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not (install_dir / ".opensre-app.install.lock").exists()


def test_installer_rejects_junction_layout_without_touching_target(tmp_path: Path) -> None:
    install_dir = tmp_path / "junction install"
    install_dir.mkdir()
    outside = tmp_path / "outside owned layout"
    outside.mkdir()
    sentinel = outside / "user-data.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    layout_root = install_dir / ".opensre-app"
    linked = _run_powershell(
        f"""
New-Item `
    -ItemType Junction `
    -Path {_ps_literal(layout_root)} `
    -Target {_ps_literal(outside)} | Out-Null
""",
        cwd=tmp_path,
    )
    assert linked.returncode == 0, linked.stdout + linked.stderr
    binary = _make_onedir_bundle(tmp_path / "junction replacement")

    completed, result = _install_bundle(
        binary_path=binary,
        install_dir=install_dir,
        install_id="junction-refused",
        cwd=tmp_path,
        check=False,
    )

    assert completed.returncode != 0
    assert result is None
    assert "reparse point" in completed.stderr
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert not (outside / "current.txt").exists()
    assert not (install_dir / "opensre.cmd").exists()


def test_update_context_rejects_unowned_versioned_layout(tmp_path: Path) -> None:
    executable = tmp_path / "unowned" / ".opensre-app" / "versions" / "build-one" / "opensre.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")

    completed, context = _resolve_install_context(
        cwd=tmp_path,
        update_executable=executable,
        parent_process_id=913,
        parent_started="unowned-start",
        installer_override=_parent_context_override(executable, pid=913, started="unowned-start"),
        check=False,
    )

    assert completed.returncode != 0
    assert context is None
    assert "unowned OpenSRE versioned update path" in completed.stderr


@pytest.mark.parametrize("malformed_parent", ("build-one", "versions"))
def test_update_context_rejects_malformed_managed_layout_path(
    tmp_path: Path,
    malformed_parent: str,
) -> None:
    executable = tmp_path / "malformed" / ".opensre-app" / malformed_parent / "opensre.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")

    completed, context = _resolve_install_context(
        cwd=tmp_path,
        update_executable=executable,
        parent_process_id=914,
        parent_started="malformed-start",
        installer_override=_parent_context_override(executable, pid=914, started="malformed-start"),
        check=False,
    )

    assert completed.returncode != 0
    assert context is None
    assert "malformed OpenSRE versioned update path" in completed.stderr


def test_historical_onefile_parent_infers_custom_update_context(tmp_path: Path) -> None:
    assert _POWERSHELL is not None
    install_dir = tmp_path / "historical custom install"
    install_dir.mkdir()
    legacy_executable = install_dir / "opensre.exe"
    shutil.copy2(Path(os.environ["COMSPEC"]), legacy_executable)
    probe_script = tmp_path / "parent-probe.ps1"
    probe_script.write_text(
        f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(INSTALL_PS1)} -SkipMain
Remove-Item Env:OPENSRE_INSTALL_DIR -ErrorAction SilentlyContinue
Remove-Item Env:OPENSRE_UPDATE_EXECUTABLE -ErrorAction SilentlyContinue
Remove-Item Env:OPENSRE_UPDATE_PARENT_PID -ErrorAction SilentlyContinue
Remove-Item Env:OPENSRE_UPDATE_PARENT_STARTED -ErrorAction SilentlyContinue
$context = Resolve-OpenSreInstallContext
$payload = [ordered]@{{
    InstallDir = [string]$context.InstallDir
    ParentProcessId = [int]$context.ParentProcessId
    IsUpdate = [bool]$context.IsUpdate
    LegacyBinaryPath = [string]$context.LegacyBinaryPath
}}
Write-Output ({_ps_literal(_CONTEXT_PREFIX)} + ($payload | ConvertTo-Json -Compress))
""",
        encoding="utf-8",
    )
    command = (
        f"{_POWERSHELL} -NoLogo -NoProfile -NonInteractive "
        f"-ExecutionPolicy Bypass -File {probe_script}"
    )
    env = _powershell_env()
    env.pop("OPENSRE_INSTALL_DIR", None)
    env.pop("OPENSRE_UPDATE_EXECUTABLE", None)
    env.pop("OPENSRE_UPDATE_PARENT_PID", None)
    env.pop("OPENSRE_UPDATE_PARENT_STARTED", None)

    completed = subprocess.run(
        [str(legacy_executable), "/d", "/s", "/c", command],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    context = _prefixed_json(completed.stdout, _CONTEXT_PREFIX)
    assert Path(context["InstallDir"]) == install_dir
    assert context["ParentProcessId"] > 0
    assert context["IsUpdate"] is True
    assert Path(context["LegacyBinaryPath"]) == legacy_executable


def test_onedir_upgrade_replaces_internal_tree_and_cleans_old_version(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "bin"
    first_binary = _make_onedir_bundle(
        tmp_path / "first bundle",
        payload={
            "old-only.txt": "old",
            "shared.txt": "old value",
        },
    )
    _, first = _install_bundle(
        binary_path=first_binary,
        install_dir=install_dir,
        install_id="build-one",
        cwd=tmp_path,
    )
    assert first is not None
    first_app_root = _path(first, "AppRoot")

    second_binary = _make_onedir_bundle(
        tmp_path / "second bundle",
        payload={
            "new-only.txt": "new",
            "shared.txt": "new value",
        },
    )
    _, second = _install_bundle(
        binary_path=second_binary,
        install_dir=install_dir,
        install_id="build-two",
        cwd=tmp_path,
    )
    assert second is not None
    second_app_root = _path(second, "AppRoot")
    second_internal = second_app_root / "_internal"

    assert second_app_root != first_app_root
    _wait_until(lambda: not first_app_root.exists())
    assert not (second_internal / "old-only.txt").exists()
    assert (second_internal / "new-only.txt").read_text(encoding="utf-8") == "new"
    assert (second_internal / "shared.txt").read_text(encoding="utf-8") == "new value"
    assert _probe_launcher(_path(second, "LauncherPath"), cwd=tmp_path)["VersionExit"] == 0


def test_deferred_cleanup_never_deletes_a_newer_active_bundle(tmp_path: Path) -> None:
    install_dir = tmp_path / "overlapping updates"
    first_binary = _make_onedir_bundle(tmp_path / "overlap first")
    _, first = _install_bundle(
        binary_path=first_binary,
        install_dir=install_dir,
        install_id="overlap-one",
        cwd=tmp_path,
    )
    assert first is not None

    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"], stdin=subprocess.PIPE
    )
    try:
        second_binary = _make_onedir_bundle(tmp_path / "overlap second")
        _, second = _install_bundle(
            binary_path=second_binary,
            install_dir=install_dir,
            install_id="overlap-two",
            cwd=tmp_path,
            parent_process_id=holder.pid,
        )
        assert second is not None
        assert second["DeferredCleanup"] is True

        third_binary = _make_onedir_bundle(tmp_path / "overlap third")
        _, third = _install_bundle(
            binary_path=third_binary,
            install_dir=install_dir,
            install_id="overlap-three",
            cwd=tmp_path,
        )
        assert third is not None
        third_root = _path(third, "AppRoot")

        holder.terminate()
        holder.wait(timeout=15)
        second_cleanup = _path(second, "CleanupPath")
        third_cleanup = _path(third, "CleanupPath")
        _wait_until(lambda: not second_cleanup.exists() and not third_cleanup.exists())

        assert third_root.is_dir()
        pointer = install_dir / ".opensre-app" / "current.txt"
        assert pointer.read_text(encoding="utf-8").strip() == "overlap-three"
        assert _probe_launcher(_path(third, "LauncherPath"), cwd=tmp_path)["VersionExit"] == 0
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)


def test_deferred_cleanup_uses_current_desktop_powershell_when_systemroot_is_poisoned(
    tmp_path: Path,
) -> None:
    assert _POWERSHELL is not None
    captured_path = tmp_path / "cleanup-powershell-path.txt"
    poisoned_system_root = tmp_path / "attacker controlled Windows"
    layout_root = tmp_path / "cleanup host install" / ".opensre-app"
    layout_root.mkdir(parents=True)
    target = layout_root / "versions" / "old-build"

    completed = _run_powershell(
        f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(INSTALL_PS1)} -SkipMain
function ConvertTo-Json {{
    param([object]$InputObject, [int]$Depth, [switch]$Compress)
    return '{{"unused":true}}'
}}
# Compile native helpers before poisoning the environment used by the .NET compiler.
Initialize-OpenSreNativePathApi
Initialize-OpenSreInstallLockNativeApi
$env:SystemRoot = {_ps_literal(poisoned_system_root)}
function Start-Process {{
    [CmdletBinding()]
    param(
        [string]$FilePath,
        [object[]]$ArgumentList,
        [string]$WindowStyle
    )
    $expectedLockPath = {_ps_literal(layout_root.parent / ".opensre-app.install.lock")}
    if (-not [System.IO.File]::Exists($expectedLockPath)) {{
        throw 'The deferred cleanup lock was not created before process launch.'
    }}
    $unexpectedLock = $null
    $lockBlocked = $false
    try {{
        $unexpectedLock = [System.IO.File]::Open(
            $expectedLockPath,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
    }}
    catch {{
        $lockBlocked = $true
        # The scheduler must retain its exclusive lease through process launch.
    }}
    if (-not $lockBlocked) {{
        if ($null -ne $unexpectedLock) {{
            $unexpectedLock.Dispose()
        }}
        throw 'The deferred cleanup lock was released before process launch.'
    }}
    [System.IO.File]::WriteAllText({_ps_literal(captured_path)}, $FilePath)
}}
$cleanup = Start-OpenSreDeferredCleanup `
    -LayoutRoot {_ps_literal(layout_root)} `
    -TargetPaths @({_ps_literal(target)}) `
    -ParentProcessId 0
[System.IO.File]::Delete((ConvertTo-OpenSreExtendedPath -Path $cleanup))
""",
        cwd=tmp_path,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    launched_path = captured_path.read_text(encoding="utf-8")
    assert launched_path.casefold() == str(_POWERSHELL).casefold()
    assert not launched_path.startswith(str(poisoned_system_root))


def test_deferred_cleanup_rejects_layout_junction_swap_after_wait(tmp_path: Path) -> None:
    install_dir = tmp_path / "cleanup junction race"
    layout_root = install_dir / ".opensre-app"
    old_target = layout_root / "versions" / "old-build"
    old_target.mkdir(parents=True)
    (old_target / "opensre.exe").write_bytes(b"old bundle")
    (layout_root / "current.txt").write_text("active-build\n", encoding="utf-8")
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    cleanup_path: Path | None = None
    preserved_layout = install_dir / "preserved-layout"
    outside = tmp_path / "outside cleanup ownership"
    escaped_target = outside / "versions" / "old-build"
    escaped_target.mkdir(parents=True)
    sentinel = escaped_target / "user-data.txt"
    sentinel.write_text("preserve", encoding="utf-8")

    try:
        scheduled = _run_powershell(
            f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(INSTALL_PS1)} -SkipMain
$cleanup = Start-OpenSreDeferredCleanup `
    -LayoutRoot {_ps_literal(layout_root)} `
    -TargetPaths @({_ps_literal(old_target)}) `
    -ParentProcessId {holder.pid}
Write-Output ('__OPENSRE_CLEANUP__' + [string]$cleanup)
""",
            cwd=tmp_path,
        )
        assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
        cleanup_lines = [
            line.removeprefix("__OPENSRE_CLEANUP__")
            for line in scheduled.stdout.splitlines()
            if line.startswith("__OPENSRE_CLEANUP__")
        ]
        assert len(cleanup_lines) == 1
        cleanup_path = Path(cleanup_lines[0])
        layout_root.rename(preserved_layout)
        linked = _run_powershell(
            f"""
New-Item `
    -ItemType Junction `
    -Path {_ps_literal(layout_root)} `
    -Target {_ps_literal(outside)} | Out-Null
""",
            cwd=tmp_path,
        )
        assert linked.returncode == 0, linked.stdout + linked.stderr
        holder.terminate()
        holder.wait(timeout=10)
        _wait_until(lambda: cleanup_path is not None and not cleanup_path.exists())

        assert sentinel.read_text(encoding="utf-8") == "preserve"
        assert (preserved_layout / "versions" / "old-build" / "opensre.exe").is_file()
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)
        if layout_root.exists():
            os.rmdir(layout_root)
        if preserved_layout.exists() and not layout_root.exists():
            preserved_layout.rename(layout_root)


def test_deferred_cleanup_rejects_layout_replaced_after_scheduling(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "cleanup layout replacement"
    layout_root, target = _make_managed_cleanup_target(install_dir)
    preserved_layout = install_dir / "preserved-layout"
    replacement_target = layout_root / "versions" / "old-build"
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    cleanup_path: Path | None = None

    try:
        scheduled, cleanup_path = _schedule_deferred_cleanup(
            installer_path=INSTALL_PS1,
            layout_root=layout_root,
            target=target,
            cwd=tmp_path,
            parent_process_id=holder.pid,
        )
        assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
        assert cleanup_path is not None

        layout_root.rename(preserved_layout)
        replacement_target.mkdir(parents=True)
        replacement_sentinel = replacement_target / "user-data.txt"
        replacement_sentinel.write_text("preserve replacement layout", encoding="utf-8")
        holder.terminate()
        holder.wait(timeout=10)
        _wait_until(lambda: cleanup_path is not None and not cleanup_path.exists())

        assert (preserved_layout / "versions" / "old-build" / "opensre.exe").is_file()
        assert replacement_sentinel.read_text(encoding="utf-8") == "preserve replacement layout"
        assert not list(layout_root.glob("retired-*"))
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)


def test_deferred_cleanup_preserves_target_replaced_after_scheduling(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "cleanup scheduled replacement"
    layout_root, target = _make_managed_cleanup_target(install_dir)
    preserved_target = target.with_name("preserved-old-build")
    replacement_sentinel = target / "user-data.txt"
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    installer = tmp_path / "install-with-short-cleanup-lock-timeout.ps1"
    installer.write_text(
        _set_embedded_cleanup_lock_timeout(
            INSTALL_PS1.read_text(encoding="utf-8"),
            seconds=2,
        ),
        encoding="utf-8",
    )
    cleanup_path: Path | None = None

    try:
        scheduled, cleanup_path = _schedule_deferred_cleanup(
            installer_path=installer,
            layout_root=layout_root,
            target=target,
            cwd=tmp_path,
            parent_process_id=holder.pid,
        )
        assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
        assert cleanup_path is not None

        target.rename(preserved_target)
        target.mkdir()
        replacement_sentinel.write_text("preserve replacement", encoding="utf-8")
        holder.terminate()
        holder.wait(timeout=10)
        _wait_until(lambda: cleanup_path is not None and not cleanup_path.exists())

        assert (preserved_target / "opensre.exe").is_file()
        assert replacement_sentinel.read_text(encoding="utf-8") == "preserve replacement"
        assert not list(layout_root.glob("retired-*"))
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)


def test_deferred_cleanup_preserves_target_appearing_after_scheduling(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "cleanup scheduled appearance"
    layout_root = install_dir / ".opensre-app"
    target = layout_root / "versions" / "old-build"
    target.parent.mkdir(parents=True)
    (layout_root / "current.txt").write_text("active-build\n", encoding="utf-8")
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    cleanup_path: Path | None = None

    try:
        scheduled, cleanup_path = _schedule_deferred_cleanup(
            installer_path=INSTALL_PS1,
            layout_root=layout_root,
            target=target,
            cwd=tmp_path,
            parent_process_id=holder.pid,
        )
        assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
        assert cleanup_path is not None

        target.mkdir()
        sentinel = target / "user-data.txt"
        sentinel.write_text("preserve appearance", encoding="utf-8")
        holder.terminate()
        holder.wait(timeout=10)
        _wait_until(lambda: cleanup_path is not None and not cleanup_path.exists())

        assert sentinel.read_text(encoding="utf-8") == "preserve appearance"
        assert not list(layout_root.glob("retired-*"))
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)


def test_deferred_cleanup_restores_target_after_late_layout_swap(tmp_path: Path) -> None:
    install_dir = tmp_path / "late cleanup layout swap"
    layout_root, target = _make_managed_cleanup_target(install_dir)
    preserved_layout = install_dir / "preserved-layout"
    outside_layout = tmp_path / "outside late cleanup layout"
    outside_target = outside_layout / "versions" / "old-build"
    outside_target.mkdir(parents=True)
    outside_binary = outside_target / "opensre.exe"
    outside_binary.write_bytes(b"unrelated outside executable")
    outside_sentinel = outside_target / "user-data.txt"
    outside_sentinel.write_text("preserve", encoding="utf-8")
    (outside_layout / "current.txt").write_text("active-build\n", encoding="utf-8")
    installer = tmp_path / "install-with-late-cleanup-layout-swap.ps1"
    swapped_source = _inject_late_cleanup_layout_swap(
        INSTALL_PS1.read_text(encoding="utf-8"),
        preserved_layout=preserved_layout,
        outside_layout=outside_layout,
    )
    installer.write_text(
        _set_embedded_cleanup_lock_timeout(swapped_source, seconds=2),
        encoding="utf-8",
    )
    cleanup_path: Path | None = None

    try:
        scheduled, cleanup_path = _schedule_deferred_cleanup(
            installer_path=installer,
            layout_root=layout_root,
            target=target,
            cwd=tmp_path,
        )
        assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
        assert cleanup_path is not None
        _wait_until(lambda: cleanup_path is not None and not cleanup_path.exists())

        assert outside_binary.read_bytes() == b"unrelated outside executable"
        assert outside_sentinel.read_text(encoding="utf-8") == "preserve"
        assert (preserved_layout / "versions" / "old-build" / "opensre.exe").is_file()
        assert list(outside_layout.glob("retired-*")) == []
    finally:
        if layout_root.is_junction():
            os.rmdir(layout_root)
        if preserved_layout.exists() and not layout_root.exists():
            preserved_layout.rename(layout_root)


def test_deferred_cleanup_holds_target_identity_through_physical_deletion(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "cleanup deletion identity"
    layout_root, target = _make_managed_cleanup_target(install_dir)
    preserved_target = install_dir / "preserved-retired-target"
    marker = tmp_path / "cleanup-deletion-swap-result.txt"
    installer = tmp_path / "install-with-cleanup-deletion-swap.ps1"
    installer.write_text(
        _inject_cleanup_deletion_target_swap(
            INSTALL_PS1.read_text(encoding="utf-8"),
            preserved_target=preserved_target,
            marker_path=marker,
        ),
        encoding="utf-8",
    )
    cleanup_path: Path | None = None

    try:
        scheduled, cleanup_path = _schedule_deferred_cleanup(
            installer_path=installer,
            layout_root=layout_root,
            target=target,
            cwd=tmp_path,
        )
        assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
        assert cleanup_path is not None
        _wait_until(lambda: cleanup_path is not None and not cleanup_path.exists())

        assert marker.read_text(encoding="utf-8") == "blocked"
        assert not target.exists()
        assert not list(layout_root.glob("retired-*"))
        assert not preserved_target.exists()
    finally:
        if preserved_target.exists():
            shutil.rmtree(preserved_target)


def test_deferred_cleanup_never_creates_lock_through_swapped_install_junction(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "cleanup lock race"
    layout_root, target = _make_managed_cleanup_target(install_dir)
    preserved_install = tmp_path / "preserved cleanup lock install"
    outside = tmp_path / "outside cleanup lock"
    outside_target = outside / ".opensre-app" / "versions" / "old-build"
    outside_target.mkdir(parents=True)
    sentinel = outside_target / "user-data.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    ready = tmp_path / "cleanup-lock-open-ready.txt"
    proceed = tmp_path / "cleanup-lock-open-continue.txt"
    installer = tmp_path / "install-with-cleanup-lock-barrier.ps1"
    installer.write_text(
        _inject_cleanup_lock_open_barrier(
            INSTALL_PS1.read_text(encoding="utf-8"),
            ready_path=ready,
            continue_path=proceed,
        ),
        encoding="utf-8",
    )
    cleanup_path: Path | None = None

    try:
        scheduled, cleanup_path = _schedule_deferred_cleanup(
            installer_path=installer,
            layout_root=layout_root,
            target=target,
            cwd=tmp_path,
        )
        assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
        assert cleanup_path is not None
        _wait_until(ready.exists)

        install_dir.rename(preserved_install)
        linked = _run_powershell(
            f"""
New-Item `
    -ItemType Junction `
    -Path {_ps_literal(install_dir)} `
    -Target {_ps_literal(outside)} | Out-Null
""",
            cwd=tmp_path,
        )
        assert linked.returncode == 0, linked.stdout + linked.stderr
        proceed.write_text("continue", encoding="utf-8")
        _wait_until(lambda: cleanup_path is not None and not cleanup_path.exists())

        assert not (outside / ".opensre-app.install.lock").exists()
        assert sentinel.read_text(encoding="utf-8") == "preserve"
        assert (preserved_install / ".opensre-app.install.lock").is_file()
        assert (
            preserved_install / ".opensre-app" / "versions" / "old-build" / "opensre.exe"
        ).is_file()
    finally:
        proceed.write_text("continue", encoding="utf-8")
        if install_dir.is_junction():
            os.rmdir(install_dir)
        if preserved_install.exists() and not install_dir.exists():
            preserved_install.rename(install_dir)


def test_deferred_cleanup_fails_closed_when_parent_metadata_is_uncertain(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "uncertain cleanup parent"
    layout_root, target = _make_managed_cleanup_target(install_dir)
    installer = tmp_path / "install-with-uncertain-cleanup-parent.ps1"
    installer.write_text(
        _inject_uncertain_parent_metadata(INSTALL_PS1.read_text(encoding="utf-8")),
        encoding="utf-8",
    )

    scheduled, cleanup_path = _schedule_deferred_cleanup(
        installer_path=installer,
        layout_root=layout_root,
        target=target,
        cwd=tmp_path,
        parent_process_id=5935,
        parent_executable_path=target / "opensre.exe",
        parent_started="1",
    )

    assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
    assert cleanup_path is not None
    _wait_until(
        lambda: cleanup_path is not None and not cleanup_path.exists(),
        timeout=_DETACHED_CLEANUP_TIMEOUT,
    )
    assert target.is_dir()
    assert (target / "opensre.exe").is_file()


@pytest.mark.parametrize(
    ("has_exited", "expect_cleanup"),
    [
        pytest.param("true", True, id="confirmed-exit"),
        pytest.param("false", False, id="still-running"),
        pytest.param("non-bool", False, id="non-boolean-state"),
        pytest.param("throw", False, id="state-inspection-error"),
    ],
)
def test_deferred_cleanup_requires_confirmed_exit_with_readable_parent_metadata(
    tmp_path: Path,
    has_exited: str,
    expect_cleanup: bool,
) -> None:
    install_dir = tmp_path / "confirmed exited cleanup parent"
    layout_root, target = _make_managed_cleanup_target(install_dir)
    parent_executable = _fake_opensre_executable()
    installer = tmp_path / f"install-with-{has_exited}-parent-state.ps1"
    source = _inject_parent_with_readable_metadata(
        INSTALL_PS1.read_text(encoding="utf-8"),
        executable=parent_executable,
        started=1,
        has_exited=has_exited,
    )
    installer.write_text(
        _set_embedded_cleanup_parent_timeout(source, seconds=3),
        encoding="utf-8",
    )

    scheduled, cleanup_path = _schedule_deferred_cleanup(
        installer_path=installer,
        layout_root=layout_root,
        target=target,
        cwd=tmp_path,
        parent_process_id=2_147_483_647,
        parent_executable_path=parent_executable,
        parent_started="1",
    )

    assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
    assert cleanup_path is not None
    _wait_until(
        lambda: cleanup_path is not None and not cleanup_path.exists(),
        timeout=_DETACHED_CLEANUP_TIMEOUT,
    )
    assert target.exists() is not expect_cleanup
    assert not list(layout_root.glob("retired-*"))


def test_deferred_cleanup_accepts_parent_exit_during_metadata_inspection(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "cleanup parent exits during metadata"
    layout_root, target = _make_managed_cleanup_target(install_dir)
    parent_executable = _fake_opensre_executable()
    installer = tmp_path / "install-with-parent-metadata-exit.ps1"
    installer.write_text(
        _inject_parent_exit_during_metadata(
            INSTALL_PS1.read_text(encoding="utf-8"),
            started=1,
        ),
        encoding="utf-8",
    )

    scheduled, cleanup_path = _schedule_deferred_cleanup(
        installer_path=installer,
        layout_root=layout_root,
        target=target,
        cwd=tmp_path,
        parent_process_id=2_147_483_647,
        parent_executable_path=parent_executable,
        parent_started="1",
    )

    assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
    assert cleanup_path is not None
    _wait_until(
        lambda: cleanup_path is not None and not cleanup_path.exists(),
        timeout=_DETACHED_CLEANUP_TIMEOUT,
    )
    assert not target.exists()
    assert not list(layout_root.glob("retired-*"))


@pytest.mark.parametrize("busy_after_exited", [False, True])
def test_deferred_cleanup_skips_confirmed_exited_process_during_scan(
    tmp_path: Path,
    busy_after_exited: bool,
) -> None:
    install_dir = tmp_path / "confirmed exited cleanup scan"
    layout_root, target = _make_managed_cleanup_target(install_dir)
    installer = tmp_path / "install-with-confirmed-exited-scan.ps1"
    source = _inject_exited_process_with_unavailable_path(
        INSTALL_PS1.read_text(encoding="utf-8"),
        busy_executable=target / "opensre.exe" if busy_after_exited else None,
    )
    installer.write_text(
        _set_embedded_cleanup_lock_timeout(source, seconds=3),
        encoding="utf-8",
    )

    scheduled, cleanup_path = _schedule_deferred_cleanup(
        installer_path=installer,
        layout_root=layout_root,
        target=target,
        cwd=tmp_path,
    )

    assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
    assert cleanup_path is not None
    _wait_until(
        lambda: cleanup_path is not None and not cleanup_path.exists(),
        timeout=_DETACHED_CLEANUP_TIMEOUT,
    )
    assert target.exists() is busy_after_exited
    assert not list(layout_root.glob("retired-*"))


def test_deferred_cleanup_rechecks_process_before_reporting_target_busy(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "cleanup process exits after target match"
    layout_root, target = _make_managed_cleanup_target(install_dir)
    installer = tmp_path / "install-with-process-exit-after-target-match.ps1"
    installer.write_text(
        _inject_process_exit_after_target_match(
            INSTALL_PS1.read_text(encoding="utf-8"),
            executable=target / "opensre.exe",
        ),
        encoding="utf-8",
    )

    scheduled, cleanup_path = _schedule_deferred_cleanup(
        installer_path=installer,
        layout_root=layout_root,
        target=target,
        cwd=tmp_path,
    )

    assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
    assert cleanup_path is not None
    _wait_until(
        lambda: cleanup_path is not None and not cleanup_path.exists(),
        timeout=_DETACHED_CLEANUP_TIMEOUT,
    )
    assert not target.exists()
    assert not list(layout_root.glob("retired-*"))


def test_deferred_cleanup_fails_closed_for_unusable_process_name(tmp_path: Path) -> None:
    install_dir = tmp_path / "cleanup unusable process name"
    layout_root, target = _make_managed_cleanup_target(install_dir)
    installer = tmp_path / "install-with-unusable-process-name.ps1"
    source = _inject_process_with_unusable_name(INSTALL_PS1.read_text(encoding="utf-8"))
    installer.write_text(
        _set_embedded_cleanup_lock_timeout(source, seconds=3),
        encoding="utf-8",
    )

    scheduled, cleanup_path = _schedule_deferred_cleanup(
        installer_path=installer,
        layout_root=layout_root,
        target=target,
        cwd=tmp_path,
    )

    assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
    assert cleanup_path is not None
    _wait_until(
        lambda: cleanup_path is not None and not cleanup_path.exists(),
        timeout=_DETACHED_CLEANUP_TIMEOUT,
    )
    assert target.is_dir()
    assert (target / "opensre.exe").is_file()
    assert not list(layout_root.glob("retired-*"))


def test_deferred_cleanup_waits_when_verified_parent_path_was_retired(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "retired cleanup parent"
    layout_root, target = _make_managed_cleanup_target(install_dir)
    expected_parent_path = install_dir / "opensre.exe"
    retired_parent_path = layout_root / "parent-image-after-retirement.exe"
    shutil.copy2(_fake_opensre_executable(), expected_parent_path)
    release = tmp_path / "release retired cleanup parent"
    parent = subprocess.Popen([str(expected_parent_path), "hold-until", str(release)])
    cleanup_path: Path | None = None
    parent_observed = tmp_path / "retired-parent-observed"

    try:
        parent_started = _process_started_token(parent.pid, cwd=tmp_path)
        expected_parent_path.rename(retired_parent_path)
        installer = tmp_path / "install-with-retired-parent-metadata-error.ps1"
        source = _inject_retired_parent_path_metadata_error(INSTALL_PS1.read_text(encoding="utf-8"))
        state_anchor = "        $parentState = Get-OpenSreParentIdentityState\n"
        assert source.count(state_anchor) == 1
        source = source.replace(
            state_anchor,
            state_anchor
            + "        if ($parentState -ceq 'running') { "
            + f"[System.IO.File]::WriteAllText({_ps_literal(parent_observed)}, 'ready') }}\n",
            1,
        )
        installer.write_text(source, encoding="utf-8")
        scheduled, cleanup_path = _schedule_deferred_cleanup(
            installer_path=installer,
            layout_root=layout_root,
            target=target,
            cwd=tmp_path,
            parent_process_id=parent.pid,
            parent_executable_path=expected_parent_path,
            parent_started=parent_started,
        )
        assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
        assert cleanup_path is not None
        _wait_until(lambda: parent_observed.is_file() or not cleanup_path.exists())
        assert parent_observed.is_file()
        assert parent.poll() is None
        assert target.is_dir()

        release.write_text("release", encoding="utf-8")
        parent.wait(timeout=10)
        _wait_until(
            lambda: cleanup_path is not None and not cleanup_path.exists(),
            timeout=_DETACHED_CLEANUP_TIMEOUT,
        )

        assert not target.exists()
        assert not list(layout_root.glob("retired-*"))
    finally:
        release.write_text("release", encoding="utf-8")
        if parent.poll() is None:
            parent.terminate()
            parent.wait(timeout=10)


def test_deferred_cleanup_blocks_late_launch_until_retirement_scan_finishes(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "late cleanup launch"
    layout_root, target = _make_managed_cleanup_target(install_dir)
    marker = tmp_path / "late-cleanup-launch-result.txt"
    installer = tmp_path / "install-with-late-cleanup-launch.ps1"
    scoped_source = _inject_owned_process_enumerator(
        INSTALL_PS1.read_text(encoding="utf-8"),
        process_id=2_147_483_000,
        process_started="0",
    )
    installer.write_text(
        _inject_late_cleanup_launch_probe(
            scoped_source,
            marker_path=marker,
        ),
        encoding="utf-8",
    )

    scheduled, cleanup_path = _schedule_deferred_cleanup(
        installer_path=installer,
        layout_root=layout_root,
        target=target,
        cwd=tmp_path,
    )

    assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
    assert cleanup_path is not None
    _wait_until(lambda: cleanup_path is not None and not cleanup_path.exists())
    assert marker.read_text(encoding="utf-8") == "blocked"
    assert not target.exists()
    assert not list(layout_root.glob("retired-*"))


def test_deferred_upgrade_removes_long_old_version_tree(tmp_path: Path) -> None:
    short_install_dir = tmp_path / "i"
    old_install_id = "long-old-" + ("o" * 32)
    first_binary = _make_onedir_bundle(tmp_path / "long cleanup first")
    _, first = _install_bundle(
        binary_path=first_binary,
        install_dir=short_install_dir,
        install_id=old_install_id,
        cwd=tmp_path,
    )
    assert first is not None
    old_root = _path(first, "AppRoot")
    payload_dir = old_root / "_internal"
    while len(str(payload_dir / "payload.txt")) <= 220:
        payload_dir /= "nested-content-filter"
    payload_dir.mkdir(parents=True, exist_ok=True)
    relative_payload = (payload_dir / "payload.txt").relative_to(short_install_dir)
    (payload_dir / "payload.txt").write_text("old", encoding="utf-8")

    install_name_prefix = "upgrade path with spaces-"
    unpadded_layout_root = tmp_path / install_name_prefix / ".opensre-app"
    padding_length = 220 - len(str(unpadded_layout_root))
    assert padding_length > 0
    install_dir = tmp_path / (install_name_prefix + ("x" * padding_length))
    short_install_dir.rename(install_dir)
    old_root = install_dir / ".opensre-app" / "versions" / old_install_id
    layout_root = install_dir / ".opensre-app"
    assert len(str(layout_root / "stage-long-new")) < 248
    assert len(str(layout_root / ("current-" + ("f" * 32) + ".tmp"))) > 260
    assert len(str(old_root / "opensre.exe")) >= 260
    assert len(str(install_dir / relative_payload)) > 260
    unrelated = install_dir / "unrelated.txt"
    unrelated.write_text("keep", encoding="utf-8")
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )

    try:
        second_binary = _make_onedir_bundle(tmp_path / "long cleanup second")
        _, second = _install_bundle(
            binary_path=second_binary,
            install_dir=install_dir,
            install_id="long-new",
            cwd=tmp_path,
            parent_process_id=holder.pid,
        )
        assert second is not None
        assert second["DeferredCleanup"] is True
        cleanup_path = _path(second, "CleanupPath")
        assert os.path.samefile(cleanup_path.parent, tempfile.gettempdir())
        new_root = _path(second, "AppRoot")

        holder.terminate()
        holder.wait(timeout=15)
        deadline = time.monotonic() + 90
        while old_root.exists() and time.monotonic() < deadline:
            time.sleep(0.1)

        assert not old_root.exists()
        assert new_root.is_dir()
        assert unrelated.read_text(encoding="utf-8") == "keep"
        pointer = install_dir / ".opensre-app" / "current.txt"
        assert pointer.read_text(encoding="utf-8").strip() == "long-new"
        assert _probe_launcher(_path(second, "LauncherPath"), cwd=tmp_path)["VersionExit"] == 0
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)


def test_install_revalidates_layout_after_uninstall_worker_wins_lock(tmp_path: Path) -> None:
    assert _POWERSHELL is not None
    install_dir = tmp_path / "worker wins reinstall race"
    app_root = install_dir / ".opensre-app"
    old_version = app_root / "versions" / "old-build"
    old_version.mkdir(parents=True)
    old_executable = old_version / "opensre.exe"
    shutil.copy2(Path(os.environ["COMSPEC"]), old_executable)
    (app_root / "layout-v1.marker").write_text(
        "OpenSRE Windows bundle layout v1\n", encoding="utf-8"
    )
    (app_root / "current.txt").write_text("old-build\n", encoding="utf-8")
    launcher = install_dir / "opensre.cmd"
    launcher.write_text("@echo off\n:: OpenSRE Windows launcher v1\n", encoding="utf-8")
    lock_path = install_dir / ".opensre-app.install.lock"
    lock_path.write_bytes(b"")

    replacement = _make_onedir_bundle(tmp_path / "worker wins replacement")
    lock_waiting = tmp_path / "installer-reached-lock.marker"
    allow_lock = tmp_path / "allow-installer-lock.marker"
    installer_script = tmp_path / "worker-wins-installer.ps1"
    installer_script.write_text(
        f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(INSTALL_PS1)} -SkipMain
$originalOpenInstallLock = ${{function:Open-OpenSreInstallLock}}
function Open-OpenSreInstallLock {{
    param([string]$InstallDir, [int]$TimeoutSeconds = 30)
    [System.IO.File]::WriteAllText({_ps_literal(lock_waiting)}, 'waiting')
    $gateDeadline = [System.DateTime]::UtcNow.AddSeconds(90)
    while (-not (Test-Path -LiteralPath {_ps_literal(allow_lock)})) {{
        if ([System.DateTime]::UtcNow -ge $gateDeadline) {{ throw 'installer gate timed out' }}
        Start-Sleep -Milliseconds 50
    }}
    return & $originalOpenInstallLock -InstallDir $InstallDir -TimeoutSeconds $TimeoutSeconds
}}
$result = Install-OpenSreVerifiedBundle `
    -BinaryPath {_ps_literal(replacement)} `
    -InstallDir {_ps_literal(install_dir)} `
    -InstallId 'new-build'
Write-Output ({_ps_literal(_RESULT_PREFIX)} + ($result | ConvertTo-Json -Compress))
""",
        encoding="utf-8",
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    installer: subprocess.Popen[str] | None = None

    try:
        ok, err = schedule_windows_managed_cleanup(
            executable=old_executable,
            app_root=app_root,
            launcher=launcher,
            parent_pid=holder.pid,
        )
        assert ok is True
        assert err is None
        installer = subprocess.Popen(
            [
                _POWERSHELL,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(installer_script),
            ],
            cwd=tmp_path,
            env=_powershell_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        deadline = time.monotonic() + 90
        while not lock_waiting.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert lock_waiting.is_file(), "installer never reached its lock acquisition"

        holder.terminate()
        holder.wait(timeout=10)
        deadline = time.monotonic() + 90
        while app_root.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not app_root.exists(), "uninstall worker did not win and move the old app root"

        allow_lock.write_text("continue", encoding="utf-8")
        stdout, stderr = installer.communicate(timeout=90)

        assert installer.returncode == 0, stdout + stderr
        marker = app_root / "layout-v1.marker"
        pointer = app_root / "current.txt"
        new_executable = app_root / "versions" / "new-build" / "opensre.exe"
        assert marker.read_text(encoding="utf-8").strip() == ("OpenSRE Windows bundle layout v1")
        assert pointer.read_text(encoding="utf-8").strip() == "new-build"
        assert new_executable.is_file()
        assert launcher.is_file()
        _, context = _resolve_install_context(
            cwd=tmp_path,
            update_executable=new_executable,
            parent_process_id=5844,
            parent_started="reinstalled-start",
            installer_override=_parent_context_override(
                new_executable, pid=5844, started="reinstalled-start"
            ),
        )
        assert context is not None
        assert Path(context["InstallDir"]) == install_dir
    finally:
        if holder.poll() is None:
            holder.terminate()
            holder.wait(timeout=10)
        if installer is not None and installer.poll() is None:
            installer.kill()
            installer.wait(timeout=10)


def test_failed_staged_verification_keeps_previous_install_active(tmp_path: Path) -> None:
    install_dir = tmp_path / "bin"
    first_binary = _make_onedir_bundle(
        tmp_path / "working bundle",
        payload={"working.txt": "still current"},
    )
    _, first = _install_bundle(
        binary_path=first_binary,
        install_dir=install_dir,
        install_id="working-build",
        cwd=tmp_path,
    )
    assert first is not None
    launcher = _path(first, "LauncherPath")
    first_app_root = _path(first, "AppRoot")
    before = _probe_launcher(launcher, cwd=tmp_path)
    invalid_binary = _make_invalid_onedir_bundle(tmp_path / "invalid bundle")

    failed, failed_result = _install_bundle(
        binary_path=invalid_binary,
        install_dir=install_dir,
        install_id="invalid-build",
        cwd=tmp_path,
        check=False,
    )

    assert failed.returncode != 0, failed.stdout + failed.stderr
    assert failed_result is None
    assert first_app_root.is_dir()
    assert (first_app_root / "_internal" / "working.txt").is_file()
    assert launcher.is_file()
    after = _probe_launcher(launcher, cwd=tmp_path)
    assert before["VersionExit"] == after["VersionExit"] == 0
    assert before["VersionOutput"] == after["VersionOutput"]
    assert not any(path.name == "invalid-build" for path in install_dir.rglob("*"))


def test_failed_onedir_package_smoke_keeps_previous_install_active(tmp_path: Path) -> None:
    install_dir = tmp_path / "package smoke rollback"
    first_binary = _make_onedir_bundle(tmp_path / "package smoke stable")
    _, first = _install_bundle(
        binary_path=first_binary,
        install_dir=install_dir,
        install_id="package-smoke-stable",
        cwd=tmp_path,
    )
    assert first is not None
    launcher = _path(first, "LauncherPath")
    first_app_root = _path(first, "AppRoot")
    before = _probe_launcher(launcher, cwd=tmp_path)
    failing_binary = _make_onedir_bundle(
        tmp_path / "package smoke failure",
        payload={"package-smoke-fail.txt": "fail before activation"},
    )

    failed, failed_result = _install_bundle(
        binary_path=failing_binary,
        install_dir=install_dir,
        install_id="package-smoke-failed",
        cwd=tmp_path,
        check=False,
    )

    assert failed.returncode != 0, failed.stdout + failed.stderr
    assert failed_result is None
    assert "bundle '_package-smoke' check failed" in failed.stderr
    assert first_app_root.is_dir()
    assert launcher.is_file()
    after = _probe_launcher(launcher, cwd=tmp_path)
    assert before["VersionExit"] == after["VersionExit"] == 0
    assert before["VersionOutput"] == after["VersionOutput"]
    assert not any(path.name == "package-smoke-failed" for path in install_dir.rglob("*"))


def test_later_install_retries_a_deep_failed_staging_directory(tmp_path: Path) -> None:
    install_dir = tmp_path / "deep stage retry"
    first_binary = _make_onedir_bundle(tmp_path / "deep stage stable")
    _, first = _install_bundle(
        binary_path=first_binary,
        install_dir=install_dir,
        install_id="deep-stage-stable",
        cwd=tmp_path,
    )
    assert first is not None
    invalid_binary = _make_invalid_onedir_bundle(tmp_path / "deep stage invalid")
    deep_directory = invalid_binary.parent / "_internal"
    for index in range(8):
        deep_directory /= f"segment-{index}-" + ("x" * 28)
    extended_deep_directory = "\\\\?\\" + str(deep_directory)
    created = _run_powershell(
        f"""
$directory = [System.IO.Directory]::CreateDirectory({_ps_literal(extended_deep_directory)})
[System.IO.File]::WriteAllText(
    [System.IO.Path]::Combine($directory.FullName, 'payload.dat'),
    'orphan candidate'
)
""",
        cwd=tmp_path,
    )
    assert created.returncode == 0, created.stdout + created.stderr

    failed, failed_result = _install_bundle(
        binary_path=invalid_binary,
        install_dir=install_dir,
        install_id="deep-stage-invalid",
        cwd=tmp_path,
        check=False,
        installer_override="""
function Remove-OpenSreInstallPath {
    param([string]$Path)
    throw 'forced stage cleanup failure'
}
""",
    )

    assert failed.returncode != 0
    assert failed_result is None
    orphaned_stage = install_dir / ".opensre-app" / "stage-deep-stage-invalid"
    assert orphaned_stage.is_dir()

    recovery_binary = _make_onedir_bundle(tmp_path / "deep stage recovery")
    _, recovered = _install_bundle(
        binary_path=recovery_binary,
        install_dir=install_dir,
        install_id="deep-stage-recovered",
        cwd=tmp_path,
    )

    assert recovered is not None
    _wait_until(lambda: not orphaned_stage.exists())
    assert _probe_launcher(_path(recovered, "LauncherPath"), cwd=tmp_path)["VersionExit"] == 0


def test_failed_post_switch_verification_rolls_back_pointer_and_retains_bundle(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "post switch rollback"
    first_binary = _make_onedir_bundle(tmp_path / "post switch first")
    _, first = _install_bundle(
        binary_path=first_binary,
        install_dir=install_dir,
        install_id="stable-build",
        cwd=tmp_path,
    )
    assert first is not None
    replacement_binary = _make_onedir_bundle(tmp_path / "post switch replacement")
    script = f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(INSTALL_PS1)} -SkipMain
function Get-OpenSreBinaryVersionInfo {{
    param([string]$BinaryPath)
    if ([System.IO.Path]::GetExtension($BinaryPath) -ieq '.cmd') {{
        throw 'forced launcher verification failure'
    }}
    return [pscustomobject]@{{ Text = 'opensre, version 0.1'; Version = '0.1' }}
}}
Install-OpenSreVerifiedBundle `
    -BinaryPath {_ps_literal(replacement_binary)} `
    -InstallDir {_ps_literal(install_dir)} `
    -InstallId 'failed-after-switch'
"""

    failed = _run_powershell(script, cwd=tmp_path)

    assert failed.returncode != 0
    pointer = install_dir / ".opensre-app" / "current.txt"
    assert pointer.read_text(encoding="utf-8").strip() == "stable-build"
    assert (install_dir / ".opensre-app" / "versions" / "failed-after-switch").is_dir()
    assert "retained for a later safe cleanup" in failed.stdout
    launcher = _path(first, "LauncherPath")
    assert _probe_launcher(launcher, cwd=tmp_path)["VersionExit"] == 0


def test_rollback_never_partially_deletes_activated_bundle_in_use(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "busy rollback"
    first_binary = _make_onedir_bundle(tmp_path / "busy rollback stable")
    _, first = _install_bundle(
        binary_path=first_binary,
        install_dir=install_dir,
        install_id="busy-rollback-stable",
        cwd=tmp_path,
    )
    assert first is not None
    replacement_binary = _make_onedir_bundle(
        tmp_path / "busy rollback replacement",
        payload={
            "lazy/module.dat": "must remain complete",
            "nested/shared.dat": "also retained",
        },
    )
    replacement_manifest = {
        path.relative_to(replacement_binary.parent): _sha256(path)
        for path in replacement_binary.parent.rglob("*")
        if path.is_file()
    }
    child_pid_path = tmp_path / "busy-rollback-child.pid"
    failed_install_id = "busy-rollback-failed"
    failed_root = install_dir / ".opensre-app" / "versions" / failed_install_id
    script = f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(INSTALL_PS1)} -SkipMain
function Set-OpenSreCurrentInstallId {{
    param([string]$LayoutRoot, [string]$InstallId)
    [System.IO.File]::WriteAllText(
        (Join-Path $LayoutRoot 'current.txt'),
        "$InstallId`r`n",
        (New-Object System.Text.UTF8Encoding($false))
    )
    if ($InstallId -eq {_ps_literal(failed_install_id)}) {{
        $activatedBinary = Join-Path {_ps_literal(failed_root)} 'opensre.exe'
        $childProcess = Start-Process `
            -FilePath $activatedBinary `
            -ArgumentList @('hold', '30000') `
            -WindowStyle Hidden `
            -PassThru
        [System.IO.File]::WriteAllText(
            {_ps_literal(child_pid_path)},
            [string]$childProcess.Id
        )
        Start-Sleep -Milliseconds 250
        if ($childProcess.HasExited) {{
            throw 'activated test process exited unexpectedly'
        }}
        throw 'forced pointer failure after publishing and concurrent launch'
    }}
}}
Install-OpenSreVerifiedBundle `
    -BinaryPath {_ps_literal(replacement_binary)} `
    -InstallDir {_ps_literal(install_dir)} `
    -InstallId {_ps_literal(failed_install_id)}
"""

    failed = _run_powershell(script, cwd=tmp_path)
    assert child_pid_path.is_file(), failed.stdout + failed.stderr
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))

    try:
        assert failed.returncode != 0
        assert "retained for a later safe cleanup" in failed.stdout
        pointer = install_dir / ".opensre-app" / "current.txt"
        assert pointer.read_text(encoding="utf-8").strip() == "busy-rollback-stable"
        running = _run_powershell(
            f"Get-Process -Id {child_pid} -ErrorAction Stop | Out-Null",
            cwd=tmp_path,
        )
        assert running.returncode == 0, running.stdout + running.stderr
        assert failed_root.is_dir()
        assert {
            path.relative_to(failed_root): _sha256(path)
            for path in failed_root.rglob("*")
            if path.is_file()
        } == replacement_manifest
        assert (failed_root / "_internal" / "lazy" / "module.dat").read_text(
            encoding="utf-8"
        ) == "must remain complete"
        assert _probe_launcher(_path(first, "LauncherPath"), cwd=tmp_path)["VersionExit"] == 0
    finally:
        _run_powershell(
            f"Stop-Process -Id {child_pid} -Force -ErrorAction SilentlyContinue",
            cwd=tmp_path,
        )


def test_failed_launcher_rewrite_restores_the_previous_launcher(tmp_path: Path) -> None:
    install_dir = tmp_path / "launcher rollback"
    first_binary = _make_onedir_bundle(tmp_path / "launcher rollback first")
    _, first = _install_bundle(
        binary_path=first_binary,
        install_dir=install_dir,
        install_id="launcher-stable",
        cwd=tmp_path,
    )
    assert first is not None
    launcher = _path(first, "LauncherPath")
    launcher_before = launcher.read_bytes()
    replacement = _make_onedir_bundle(tmp_path / "launcher rollback replacement")

    failed, result = _install_bundle(
        binary_path=replacement,
        install_dir=install_dir,
        install_id="launcher-invalid",
        cwd=tmp_path,
        check=False,
        installer_override=r"""
function Get-OpenSreBinaryVersionInfo {
    param([string]$BinaryPath)
    if ([System.IO.Path]::GetExtension($BinaryPath) -ieq '.cmd') {
        throw 'forced launcher verification failure'
    }
    return [pscustomobject]@{ Text = 'opensre, version 0.1'; Version = '0.1' }
}
""",
    )

    assert failed.returncode != 0
    assert result is None
    pointer = install_dir / ".opensre-app" / "current.txt"
    assert pointer.read_text(encoding="utf-8").strip() == "launcher-stable"
    assert launcher.read_bytes() == launcher_before
    assert _probe_launcher(launcher, cwd=tmp_path)["VersionExit"] == 0
    assert (install_dir / ".opensre-app" / "versions" / "launcher-invalid").is_dir()
    assert "retained for a later safe cleanup" in failed.stdout


def test_failed_activation_does_not_restore_modified_marker_owned_launcher(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "modified launcher rollback"
    first_binary = _make_onedir_bundle(tmp_path / "modified launcher first")
    _, first = _install_bundle(
        binary_path=first_binary,
        install_dir=install_dir,
        install_id="modified-launcher-stable",
        cwd=tmp_path,
    )
    assert first is not None
    launcher = _path(first, "LauncherPath")
    canonical_content = launcher.read_bytes()
    tamper_marker = tmp_path / "modified-launcher-ran.txt"
    launcher.write_bytes(
        "\r\n".join(
            (
                "@echo off",
                ":: OpenSRE Windows launcher v1",
                f'echo tampered>"{tamper_marker}"',
                f'"{_path(first, "BinaryPath")}" %*',
                "exit /b %ERRORLEVEL%",
                "",
            )
        ).encode("utf-8")
    )
    replacement = _make_onedir_bundle(tmp_path / "modified launcher replacement")

    failed, result = _install_bundle(
        binary_path=replacement,
        install_dir=install_dir,
        install_id="modified-launcher-invalid",
        cwd=tmp_path,
        check=False,
        installer_override=r"""
function Get-OpenSreBinaryVersionInfo {
    param([string]$BinaryPath)
    if ([System.IO.Path]::GetExtension($BinaryPath) -ieq '.cmd') {
        throw 'forced launcher verification failure'
    }
    return [pscustomobject]@{ Text = 'opensre, version 0.1'; Version = '0.1' }
}
""",
    )

    assert failed.returncode != 0
    assert result is None
    pointer = install_dir / ".opensre-app" / "current.txt"
    assert pointer.read_text(encoding="utf-8").strip() == "modified-launcher-stable"
    assert launcher.read_bytes() == canonical_content
    assert not tamper_marker.exists()
    assert _probe_launcher(launcher, cwd=tmp_path)["VersionExit"] == 0
    assert (install_dir / ".opensre-app" / "versions" / "modified-launcher-invalid").is_dir()
    assert "retained for a later safe cleanup" in failed.stdout


def test_failed_pointer_rollback_preserves_the_pointer_live_target(tmp_path: Path) -> None:
    install_dir = tmp_path / "rollback failure safety"
    first_binary = _make_onedir_bundle(tmp_path / "rollback failure first")
    _, first = _install_bundle(
        binary_path=first_binary,
        install_dir=install_dir,
        install_id="rollback-stable",
        cwd=tmp_path,
    )
    assert first is not None
    replacement_binary = _make_onedir_bundle(tmp_path / "rollback failure replacement")
    script = f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(INSTALL_PS1)} -SkipMain
function Set-OpenSreCurrentInstallId {{
    param([string]$LayoutRoot, [string]$InstallId)
    if ($InstallId -eq 'rollback-stable') {{
        throw 'forced rollback write failure'
    }}
    [System.IO.File]::WriteAllText(
        (Join-Path $LayoutRoot 'current.txt'),
        "$InstallId`r`n",
        (New-Object System.Text.UTF8Encoding($false))
    )
}}
function Get-OpenSreBinaryVersionInfo {{
    param([string]$BinaryPath)
    if ([System.IO.Path]::GetExtension($BinaryPath) -ieq '.cmd') {{
        throw 'forced launcher verification failure'
    }}
    return [pscustomobject]@{{ Text = 'opensre, version 0.1'; Version = '0.1' }}
}}
Install-OpenSreVerifiedBundle `
    -BinaryPath {_ps_literal(replacement_binary)} `
    -InstallDir {_ps_literal(install_dir)} `
    -InstallId 'rollback-live-target'
"""

    failed = _run_powershell(script, cwd=tmp_path)

    assert failed.returncode != 0
    pointer = install_dir / ".opensre-app" / "current.txt"
    assert pointer.read_text(encoding="utf-8").strip() == "rollback-live-target"
    live_root = install_dir / ".opensre-app" / "versions" / "rollback-live-target"
    assert live_root.is_dir()
    launcher = _path(first, "LauncherPath")
    assert _probe_launcher(launcher, cwd=tmp_path)["VersionExit"] == 0


def test_install_preserves_unrelated_install_directory_entries(tmp_path: Path) -> None:
    install_dir = tmp_path / "shared bin"
    unrelated_dir = install_dir / "another-application"
    unrelated_dir.mkdir(parents=True)
    sentinel = install_dir / "sentinel.dat"
    nested_sentinel = unrelated_dir / "state.json"
    sentinel.write_bytes(b"do not replace")
    nested_sentinel.write_text('{"owned_by":"someone_else"}', encoding="utf-8")
    binary = _make_onedir_bundle(tmp_path / "bundle")

    _, result = _install_bundle(
        binary_path=binary,
        install_dir=install_dir,
        install_id="safe-build",
        cwd=tmp_path,
    )

    assert result is not None
    assert sentinel.read_bytes() == b"do not replace"
    assert nested_sentinel.read_text(encoding="utf-8") == '{"owned_by":"someone_else"}'


_E2E_RELEASE_VERSION = "0.1.2026.8.31"
_CONFIRMATION_PROMPT_PREFIX = "__OPENSRE_CONFIRMATION_PROMPT__"


def _make_release_archive(root: Path, *, payload: dict[str, str] | None = None) -> Path:
    """Build a release-format zip that contains the complete onedir bundle."""
    bundle_root = root / "release bundle"
    _make_onedir_bundle(bundle_root, payload=payload)
    archive = root / "opensre-windows-x86_64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle_zip:
        for path in sorted(bundle_root.rglob("*")):
            if path.is_file():
                bundle_zip.write(path, path.relative_to(bundle_root).as_posix())
    checksum = Path(f"{archive}.sha256")
    checksum.write_text(f"{_sha256(archive)}  {archive.name}\n", encoding="ascii")
    return archive


def _make_onefile_release_archive(
    root: Path,
    *,
    source_binary: Path | None = None,
    nested: bool = False,
) -> Path:
    """Build a historical release zip containing only ``opensre.exe``."""
    root.mkdir(parents=True)
    binary = root / "opensre.exe"
    shutil.copy2(source_binary or _fake_opensre_executable(), binary)
    archive = root / "opensre-windows-x86_64.zip"
    archive_name = Path("opensre") / binary.name if nested else Path(binary.name)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle_zip:
        bundle_zip.write(binary, archive_name.as_posix())
    checksum = Path(f"{archive}.sha256")
    checksum.write_text(f"{_sha256(archive)}  {archive.name}\n", encoding="ascii")
    return archive


def _install_e2e_overrides(
    archive: Path, *, confirmation: str | None, include_checksum: bool = True
) -> str:
    """Stub only release discovery and download so install-context resolution stays real."""
    interactive_override = ""
    if confirmation is not None:
        interactive_override = f"""
function Test-OpenSreInteractiveHost {{
    return $true
}}
function Read-OpenSreConfirmationResponse {{
    param([Parameter(Mandatory = $true)][string]$Prompt)
    Write-Host ({_ps_literal(_CONFIRMATION_PROMPT_PREFIX)} + $Prompt)
    return {_ps_literal(confirmation)}
}}
"""
    checksum = Path(f"{archive}.sha256")
    checksum_url = _ps_literal(checksum) if include_checksum else "''"
    checksum_name = _ps_literal(checksum.name) if include_checksum else "''"
    return f"""
function Get-OpenSreReleaseMetadata {{
    param([string]$Repo, [string]$Channel, [string]$RequestedVersion)
    return [pscustomobject]@{{
        Version = {_ps_literal(_E2E_RELEASE_VERSION)}
        Release = [pscustomobject]@{{ tag_name = 'main-build' }}
    }}
}}
function Resolve-OpenSreArchiveDownload {{
    param($Release, [string]$Version, [string]$Channel, [string]$TargetArch)
    return [pscustomobject]@{{
        ArchiveName = 'opensre-windows-x86_64.zip'
        ArchiveUrl = {_ps_literal(archive)}
        ChecksumUrl = {checksum_url}
        ChecksumName = {checksum_name}
        ResolvedArch = $TargetArch
    }}
}}
function Invoke-OpenSreDownloadFileWithProgress {{
    param([string]$Uri, [string]$OutFile, [string]$Label)
    Copy-Item -LiteralPath $Uri -Destination $OutFile -Force
}}
function Ensure-OpenSreGithubCli {{ }}
function Test-OpenSreDirectoryOnPath {{
    param([string]$Directory)
    return $true
}}
function Start-OpenSreOnboardingAfterInstall {{
    param([string]$BinaryPath, [string]$DisplayName)
}}
{interactive_override}
"""


def _install_end_to_end(
    *,
    archive: Path,
    install_dir: Path,
    cwd: Path,
    confirmation: str | None,
    opt_in: str | None = None,
    include_checksum: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run the full installer entry point through normal install-context resolution."""
    opt_in_line = (
        f"$env:OPENSRE_INSTALL_REPLACE_EXISTING_BINARY = {_ps_literal(opt_in)}"
        if opt_in is not None
        else "Remove-Item Env:OPENSRE_INSTALL_REPLACE_EXISTING_BINARY -ErrorAction SilentlyContinue"
    )
    script = f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(INSTALL_PS1)} -SkipMain
{_install_e2e_overrides(archive, confirmation=confirmation, include_checksum=include_checksum)}
$env:OPENSRE_INSTALL_DIR = {_ps_literal(install_dir)}
Remove-Item Env:OPENSRE_UPDATE_EXECUTABLE -ErrorAction SilentlyContinue
Remove-Item Env:OPENSRE_UPDATE_PARENT_PID -ErrorAction SilentlyContinue
Remove-Item Env:OPENSRE_UPDATE_PARENT_STARTED -ErrorAction SilentlyContinue
{opt_in_line}
Install-OpenSre
"""
    return _run_powershell(script, cwd=cwd)


def _tree_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        snapshot[key] = _sha256(path) if path.is_file() else "<dir>"
    return snapshot


def test_install_fails_closed_when_checksum_asset_is_missing(tmp_path: Path) -> None:
    archive = _make_release_archive(tmp_path / "missing checksum release")
    install_dir = tmp_path / "missing checksum install"

    completed = _install_end_to_end(
        archive=archive,
        install_dir=install_dir,
        cwd=tmp_path,
        confirmation=None,
        include_checksum=False,
    )

    assert completed.returncode != 0
    assert "missing required checksum asset" in completed.stderr
    assert not (install_dir / ".opensre-app").exists()
    assert not (install_dir / "opensre.cmd").exists()


def test_install_fails_closed_when_checksum_does_not_match(tmp_path: Path) -> None:
    archive = _make_release_archive(tmp_path / "mismatched checksum release")
    Path(f"{archive}.sha256").write_text(f"{'0' * 64}  {archive.name}\n", encoding="ascii")
    install_dir = tmp_path / "mismatched checksum install"

    completed = _install_end_to_end(
        archive=archive,
        install_dir=install_dir,
        cwd=tmp_path,
        confirmation=None,
    )

    assert completed.returncode != 0
    assert "Checksum verification failed" in completed.stderr
    assert not (install_dir / ".opensre-app").exists()
    assert not (install_dir / "opensre.cmd").exists()


def test_root_onefile_release_installs_as_a_clean_flat_executable(tmp_path: Path) -> None:
    archive = _make_onefile_release_archive(tmp_path / "historical onefile release")
    install_dir = tmp_path / "historical onefile install"

    completed = _install_end_to_end(
        archive=archive,
        install_dir=install_dir,
        cwd=tmp_path,
        confirmation=None,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    installed_binary = install_dir / "opensre.exe"
    assert installed_binary.is_file()
    assert _sha256(installed_binary) == _sha256(
        tmp_path / "historical onefile release" / "opensre.exe"
    )
    assert not (install_dir / "opensre.cmd").exists()
    assert not (install_dir / ".opensre-app").exists()
    assert not (install_dir / ".opensre-app.install.lock").exists()
    assert {path.relative_to(install_dir) for path in install_dir.rglob("*")} == {
        Path("opensre.exe")
    }


def test_confirmed_onefile_reinstall_atomically_replaces_flat_executable(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "confirmed flat reinstall"
    install_dir.mkdir()
    target = install_dir / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), target)
    unrelated = install_dir / "unrelated.txt"
    unrelated.write_text("preserve", encoding="utf-8")
    preexisting_lock = install_dir / ".opensre-app.install.lock"
    preexisting_lock.write_text("retire after success", encoding="utf-8")
    replacement_dir = tmp_path / "confirmed flat candidate"
    replacement_dir.mkdir()
    replacement = replacement_dir / "replacement.exe"
    shutil.copy2(_fake_opensre_executable(), replacement)
    replacement.write_bytes(replacement.read_bytes() + b"replacement onefile")
    replacement_hash = _sha256(replacement)
    archive = _make_onefile_release_archive(
        tmp_path / "confirmed onefile release",
        source_binary=replacement,
    )

    completed = _install_end_to_end(
        archive=archive,
        install_dir=install_dir,
        cwd=tmp_path,
        confirmation="y",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert _sha256(target) == replacement_hash
    assert unrelated.read_text(encoding="utf-8") == "preserve"
    assert not (install_dir / "opensre.cmd").exists()
    assert not (install_dir / ".opensre-app").exists()
    assert not preexisting_lock.exists()


def test_nested_onefile_archive_is_rejected_without_install_mutation(tmp_path: Path) -> None:
    archive = _make_onefile_release_archive(
        tmp_path / "nested incomplete release",
        nested=True,
    )
    install_dir = tmp_path / "nested incomplete install"
    install_dir.mkdir()
    sentinel = install_dir / "unrelated.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    before = _tree_snapshot(install_dir)

    completed = _install_end_to_end(
        archive=archive,
        install_dir=install_dir,
        cwd=tmp_path,
        confirmation=None,
    )

    assert completed.returncode != 0
    assert "did not contain the complete OpenSRE onedir bundle" in completed.stderr
    assert _tree_snapshot(install_dir) == before


def test_onefile_release_refuses_existing_managed_install_without_mutation(
    tmp_path: Path,
) -> None:
    managed_archive = _make_release_archive(tmp_path / "managed release")
    install_dir = tmp_path / "managed install"
    installed = _install_end_to_end(
        archive=managed_archive,
        install_dir=install_dir,
        cwd=tmp_path,
        confirmation=None,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    before = _tree_snapshot(install_dir)
    onefile_archive = _make_onefile_release_archive(tmp_path / "stale onefile release")

    refused = _install_end_to_end(
        archive=onefile_archive,
        install_dir=install_dir,
        cwd=tmp_path,
        confirmation=None,
    )

    assert refused.returncode != 0
    assert "historical OpenSRE onefile release over managed application directory" in refused.stderr
    assert "Retry after a Windows onedir release is available" in refused.stderr
    assert _tree_snapshot(install_dir) == before


def test_verified_running_onefile_is_a_noop_for_identical_release(tmp_path: Path) -> None:
    install_dir = tmp_path / "running identical legacy"
    install_dir.mkdir()
    target = install_dir / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), target)
    candidate_dir = tmp_path / "identical candidate"
    candidate_dir.mkdir()
    candidate = candidate_dir / "opensre.exe"
    shutil.copy2(target, candidate)
    release = tmp_path / "release identical legacy"
    running = subprocess.Popen([str(target), "hold-until", str(release)])

    try:
        before = _tree_snapshot(install_dir)
        _, result = _install_onefile(
            binary_path=candidate,
            install_dir=install_dir,
            cwd=tmp_path,
            verified_legacy_binary_path=target,
        )

        assert result is not None
        assert _path(result, "BinaryPath") == target
        assert _path(result, "LauncherPath") == target
        assert result["LayoutRoot"] == ""
        assert running.poll() is None
        assert _tree_snapshot(install_dir) == before
        assert not (install_dir / ".opensre-app.install.lock").exists()
    finally:
        release.write_text("release", encoding="utf-8")
        running.wait(timeout=10)


def test_verified_running_onefile_refuses_different_release_without_mutation(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "running different legacy"
    install_dir.mkdir()
    target = install_dir / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), target)
    candidate_dir = tmp_path / "different candidate"
    candidate_dir.mkdir()
    candidate = candidate_dir / "opensre.exe"
    shutil.copy2(target, candidate)
    candidate.write_bytes(candidate.read_bytes() + b"different historical release")
    release = tmp_path / "release different legacy"
    running = subprocess.Popen([str(target), "hold-until", str(release)])

    try:
        before = _tree_snapshot(install_dir)
        completed, result = _install_onefile(
            binary_path=candidate,
            install_dir=install_dir,
            cwd=tmp_path,
            verified_legacy_binary_path=target,
            check=False,
        )

        assert completed.returncode != 0
        assert result is None
        assert "Retry after a Windows onedir release is available" in completed.stderr
        assert running.poll() is None
        assert _tree_snapshot(install_dir) == before
        assert not (install_dir / ".opensre-app.install.lock").exists()
    finally:
        release.write_text("release", encoding="utf-8")
        running.wait(timeout=10)


def test_verified_running_onefile_refuses_when_legacy_target_disappeared(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "missing running legacy"
    install_dir.mkdir()
    missing_target = install_dir / "opensre.exe"
    candidate_dir = tmp_path / "missing target candidate"
    candidate_dir.mkdir()
    candidate = candidate_dir / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), candidate)

    completed, result = _install_onefile(
        binary_path=candidate,
        install_dir=install_dir,
        cwd=tmp_path,
        verified_legacy_binary_path=missing_target,
        check=False,
    )

    assert completed.returncode != 0
    assert result is None
    assert "Retry after a Windows onedir release is available" in completed.stderr
    assert not missing_target.exists()
    assert not (install_dir / ".opensre-app.install.lock").exists()


def test_onefile_revalidates_approved_flat_executable_hash_under_lock(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "changed approved legacy"
    install_dir.mkdir()
    target = install_dir / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), target)
    approved_hash = _sha256(target)
    target.write_bytes(target.read_bytes() + b"changed after approval")
    changed_hash = _sha256(target)
    preexisting_lock = install_dir / ".opensre-app.install.lock"
    preexisting_lock.write_text("preserve existing lock", encoding="utf-8")
    candidate_dir = tmp_path / "approved onefile candidate"
    candidate_dir.mkdir()
    candidate = candidate_dir / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), candidate)

    completed, result = _install_onefile(
        binary_path=candidate,
        install_dir=install_dir,
        cwd=tmp_path,
        approved_legacy_binary_path=target,
        approved_legacy_binary_sha256=approved_hash,
        check=False,
    )

    assert completed.returncode != 0
    assert result is None
    assert "Refusing to replace unverified pre-existing executable" in completed.stderr
    assert target.is_file()
    assert _sha256(target) == changed_hash
    assert preexisting_lock.read_text(encoding="utf-8") == "preserve existing lock"


def test_onefile_refuses_target_swapped_after_authorization(tmp_path: Path) -> None:
    install_dir = tmp_path / "onefile post-authorization swap"
    install_dir.mkdir()
    target = install_dir / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), target)
    approved_hash = _sha256(target)
    preserved_approved = install_dir / "approved-opensre.exe"
    unrelated_bytes = b"unrelated target must remain"
    candidate_dir = tmp_path / "onefile post-authorization candidate"
    candidate_dir.mkdir()
    candidate = candidate_dir / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), candidate)
    unrelated_payload = base64.b64encode(unrelated_bytes).decode("ascii")
    override = f"""
$script:OpenSreOriginalTestInstallFileSnapshot = ${{function:Test-OpenSreInstallFileSnapshot}}
$script:OpenSreTargetSwapped = $false
function Test-OpenSreInstallFileSnapshot {{
    param([string]$Path, [object]$Expected, [switch]$AllowRelocated)
    $result = & $script:OpenSreOriginalTestInstallFileSnapshot `
        -Path $Path `
        -Expected $Expected `
        -AllowRelocated:$AllowRelocated
    if ($result -and -not $AllowRelocated -and
        -not $script:OpenSreTargetSwapped -and
        (Test-OpenSreSamePath -Left $Path -Right {_ps_literal(target)})) {{
        $script:OpenSreTargetSwapped = $true
        [System.IO.File]::Move({_ps_literal(target)}, {_ps_literal(preserved_approved)})
        [System.IO.File]::WriteAllBytes(
            {_ps_literal(target)},
            [System.Convert]::FromBase64String('{unrelated_payload}')
        )
    }}
    return $result
}}
"""

    completed, result = _install_onefile(
        binary_path=candidate,
        install_dir=install_dir,
        cwd=tmp_path,
        approved_legacy_binary_path=target,
        approved_legacy_binary_sha256=approved_hash,
        installer_override=override,
        check=False,
    )

    assert completed.returncode != 0
    assert result is None
    assert "Refusing to replace unverified pre-existing executable" in completed.stderr
    assert target.read_bytes() == unrelated_bytes
    assert _sha256(preserved_approved) == approved_hash
    assert not (install_dir / "opensre.cmd").exists()
    assert not (install_dir / ".opensre-app").exists()
    assert not (install_dir / ".opensre-app.install.lock").exists()


def _preexisting_flat_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a historical flat installation and guard its executable against execution."""
    install_dir = tmp_path / "historical install dir"
    install_dir.mkdir()
    preexisting_binary = install_dir / "opensre.exe"
    shutil.copy2(_fake_opensre_executable(), preexisting_binary)
    (install_dir / "unrelated-tool.txt").write_text("keep me", encoding="utf-8")
    monkeypatch.setenv("OPENSRE_TEST_GUARDED_EXECUTABLE", str(preexisting_binary))
    monkeypatch.setenv(
        "OPENSRE_TEST_EXECUTION_MARKER", str(tmp_path / "preexisting-executable-ran.txt")
    )
    return install_dir


def test_reinstall_over_flat_install_replaces_only_after_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_dir = _preexisting_flat_install(tmp_path, monkeypatch)
    preexisting_binary = install_dir / "opensre.exe"
    archive = _make_release_archive(tmp_path / "release")

    completed = _install_end_to_end(
        archive=archive,
        install_dir=install_dir,
        cwd=tmp_path,
        confirmation="y",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    prompts = [
        line
        for line in completed.stdout.splitlines()
        if line.startswith(_CONFIRMATION_PROMPT_PREFIX)
    ]
    assert len(prompts) == 1
    assert "[y/N]" in prompts[0]
    assert str(preexisting_binary) in completed.stdout
    assert not preexisting_binary.exists()
    assert (install_dir / "opensre.cmd").is_file()
    assert (install_dir / ".opensre-app" / "current.txt").is_file()
    assert (install_dir / "unrelated-tool.txt").read_text(encoding="utf-8") == "keep me"
    assert not (tmp_path / "preexisting-executable-ran.txt").exists()


def test_reinstall_over_flat_install_declined_changes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_dir = _preexisting_flat_install(tmp_path, monkeypatch)
    archive = _make_release_archive(tmp_path / "release")
    before = _tree_snapshot(install_dir)

    completed = _install_end_to_end(
        archive=archive,
        install_dir=install_dir,
        cwd=tmp_path,
        confirmation="n",
    )

    assert completed.returncode != 0
    assert "Refusing to replace unverified pre-existing executable" in completed.stderr
    assert _tree_snapshot(install_dir) == before
    assert not (tmp_path / "preexisting-executable-ran.txt").exists()


def test_reinstall_over_flat_install_is_fail_closed_without_a_tty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_dir = _preexisting_flat_install(tmp_path, monkeypatch)
    archive = _make_release_archive(tmp_path / "release")
    before = _tree_snapshot(install_dir)

    completed = _install_end_to_end(
        archive=archive,
        install_dir=install_dir,
        cwd=tmp_path,
        confirmation=None,
    )

    assert completed.returncode != 0
    assert "Refusing to replace unverified pre-existing executable" in completed.stderr
    assert "OPENSRE_INSTALL_REPLACE_EXISTING_BINARY=1" in completed.stderr
    assert _tree_snapshot(install_dir) == before
    assert not (tmp_path / "preexisting-executable-ran.txt").exists()


def test_reinstall_over_flat_install_rejects_nonliteral_automation_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_dir = _preexisting_flat_install(tmp_path, monkeypatch)
    archive = _make_release_archive(tmp_path / "release")
    before = _tree_snapshot(install_dir)

    completed = _install_end_to_end(
        archive=archive,
        install_dir=install_dir,
        cwd=tmp_path,
        confirmation=None,
        opt_in="true",
    )

    assert completed.returncode != 0
    assert "Refusing to replace unverified pre-existing executable" in completed.stderr
    assert _tree_snapshot(install_dir) == before
    assert not (tmp_path / "preexisting-executable-ran.txt").exists()


def test_reinstall_over_flat_install_honors_the_automation_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_dir = _preexisting_flat_install(tmp_path, monkeypatch)
    preexisting_binary = install_dir / "opensre.exe"
    archive = _make_release_archive(tmp_path / "release")

    completed = _install_end_to_end(
        archive=archive,
        install_dir=install_dir,
        cwd=tmp_path,
        confirmation=None,
        opt_in="1",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not any(
        line.startswith(_CONFIRMATION_PROMPT_PREFIX) for line in completed.stdout.splitlines()
    )
    assert not preexisting_binary.exists()
    assert (install_dir / "opensre.cmd").is_file()
    assert (install_dir / "unrelated-tool.txt").read_text(encoding="utf-8") == "keep me"
    assert not (tmp_path / "preexisting-executable-ran.txt").exists()


def test_update_migration_from_flat_install_needs_no_confirmation(tmp_path: Path) -> None:
    """`opensre update` migration stays authorized by process identity, with no prompt."""
    assert _POWERSHELL is not None
    install_dir = tmp_path / "historical update install"
    install_dir.mkdir()
    legacy_executable = install_dir / "opensre.exe"
    shutil.copy2(Path(os.environ["COMSPEC"]), legacy_executable)
    archive = _make_release_archive(tmp_path / "release")

    probe_script = tmp_path / "update-migration-probe.ps1"
    probe_script.write_text(
        f"""
$ErrorActionPreference = 'Stop'
. {_ps_literal(INSTALL_PS1)} -SkipMain
{_install_e2e_overrides(archive, confirmation=None)}
Remove-Item Env:OPENSRE_INSTALL_DIR -ErrorAction SilentlyContinue
Remove-Item Env:OPENSRE_UPDATE_EXECUTABLE -ErrorAction SilentlyContinue
Remove-Item Env:OPENSRE_UPDATE_PARENT_PID -ErrorAction SilentlyContinue
Remove-Item Env:OPENSRE_UPDATE_PARENT_STARTED -ErrorAction SilentlyContinue
Remove-Item Env:OPENSRE_INSTALL_REPLACE_EXISTING_BINARY -ErrorAction SilentlyContinue
Install-OpenSre
""",
        encoding="utf-8",
    )
    command = (
        f"{_POWERSHELL} -NoLogo -NoProfile -NonInteractive "
        f"-ExecutionPolicy Bypass -File {probe_script}"
    )
    env = _powershell_env()
    for name in (
        "OPENSRE_INSTALL_DIR",
        "OPENSRE_UPDATE_EXECUTABLE",
        "OPENSRE_UPDATE_PARENT_PID",
        "OPENSRE_UPDATE_PARENT_STARTED",
        "OPENSRE_INSTALL_REPLACE_EXISTING_BINARY",
    ):
        env.pop(name, None)

    completed = subprocess.run(
        [str(legacy_executable), "/d", "/s", "/c", command],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not any(
        line.startswith(_CONFIRMATION_PROMPT_PREFIX) for line in completed.stdout.splitlines()
    )
    assert (install_dir / "opensre.cmd").is_file()
    assert (install_dir / ".opensre-app" / "current.txt").is_file()
    assert not legacy_executable.exists()
