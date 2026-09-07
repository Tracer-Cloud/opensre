"""Contracts for the PowerShell installer progress helpers."""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

INSTALL_PS1 = Path(__file__).parents[2] / "install.ps1"


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def test_install_ps1_defines_branded_progress_helpers() -> None:
    source = INSTALL_PS1.read_text()

    for helper in (
        "function Write-OpenSreHeader",
        "function Test-OpenSreInteractiveHost",
        "function Get-OpenSreConsoleWidth",
        "function Limit-OpenSreText",
        "function Get-OpenSreFriendlyProgressLabel",
        "function Get-OpenSreProgressFrame",
        "function New-OpenSreProgressBar",
        "function Invoke-OpenSreStep",
        "function Invoke-OpenSreFirstLaunchWarmup",
        "function Invoke-OpenSreDownloadFileWithProgress",
    ):
        assert helper in source

    assert "OPENSRE_INSTALL_VERBOSE" in source
    assert '$ProgressPreference = "SilentlyContinue"' in source
    assert "$ProgressPreference = $previousProgressPreference" in source
    assert "Clear-Host" not in source
    assert "preparing installer" not in source


def test_install_ps1_avoids_ps7_only_syntax_and_write_progress() -> None:
    source = INSTALL_PS1.read_text()

    forbidden_snippets = (
        "$PSStyle",
        "??",
        "Join-String",
        "-SkipHttpErrorCheck",
        "Write-Progress",
    )
    for snippet in forbidden_snippets:
        assert snippet not in source


def test_install_ps1_preserves_retry_contract_source() -> None:
    source = INSTALL_PS1.read_text()

    assert 'Write-Warning "Attempt $attempt to $Description failed' in source
    assert "after $attempt attempts" in source
    assert "$statusCode -ge 400 -and $statusCode -lt 500" in source


def test_install_ps1_defaults_to_main_build_channel() -> None:
    source = INSTALL_PS1.read_text()

    assert 'else { "main" }' in source
    assert 'else { "main-build" }' in source
    assert "releases/tags/$mainReleaseTag" in source
    assert "$script:OpenSreChannelExplicit" in source
    assert '$resolvedChannel = "release"' in source
    assert "releases/tags/nightly" not in source


def test_install_ps1_contains_auto_onboarding_launch_hook() -> None:
    source = INSTALL_PS1.read_text()

    assert "function Test-OpenSreAutoLaunchEnabled" in source
    assert "function Start-OpenSreOnboardingAfterInstall" in source
    assert "OPENSRE_AUTO_LAUNCH" in source
    assert "& $BinaryPath setup" in source
    assert "Start-OpenSreOnboardingAfterInstall -BinaryPath $installedBinaryPath" in source
    # A redirected/piped host must be treated as non-interactive so the
    # full-screen prompt is not launched into a terminal it cannot control
    # (issue #3273).
    assert "[System.Console]::IsInputRedirected" in source


def test_install_ps1_soft_installs_github_cli_via_winget() -> None:
    source = INSTALL_PS1.read_text()

    assert "function Ensure-OpenSreGithubCli" in source
    assert "OPENSRE_SKIP_GH_INSTALL" in source
    assert "winget install --id GitHub.cli" in source
    assert "Ensure-OpenSreGithubCli" in source


def test_install_ps1_keeps_download_urls_verbose_only() -> None:
    source = INSTALL_PS1.read_text()

    assert 'Write-OpenSreDetail -Message "Download URL: $Uri"' in source
    assert 'Write-OpenSreDetail -Message "Destination: $OutFile"' in source
    assert "-Detail $downloadUrl" not in source
    assert "-Detail $checksumUrl" not in source


def test_install_ps1_uses_bounded_short_progress_labels() -> None:
    source = INSTALL_PS1.read_text()

    assert "Get-OpenSreConsoleWidth" in source
    assert "Limit-OpenSreText -Text (Get-OpenSreFriendlyProgressLabel -Label $Label)" in source
    assert "Installing OpenSRE" in source
    assert "downloading archive" in source
    assert "verifying checksum" in source
    assert '" " * 100' not in source
    # The -f expression must be fully parenthesized before Console.Write, otherwise
    # PowerShell steals the second -f argument as a Write parameter (issue #4188).
    assert '[System.Console]::Write(("`r{0}`r{1}" -f (" " * $clearWidth), $content))' in source


def test_install_ps1_progress_format_survives_console_write_precedence() -> None:
    """Regression for #4188: Console.Write + -f comma precedence on Windows."""
    shell = _powershell()
    if shell is None:
        pytest.skip("PowerShell is not installed in this environment.")

    # Mirror Write-OpenSreProgressLine's format call. The unparenthesized form
    # throws FormatException; the parenthesized form used in install.ps1 must succeed.
    script = textwrap.dedent(
        r"""
        $ErrorActionPreference = 'Stop'
        $clearWidth = 40
        $content = '  / #### Installing OpenSRE downloading archive 5%'
        [System.Console]::Write(("`r{0}`r{1}" -f (" " * $clearWidth), $content))
        Write-Output 'FORMAT_OK'
        """
    )

    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "FORMAT_OK" in (result.stdout + result.stderr)


def test_install_ps1_dot_sources_when_powershell_available() -> None:
    shell = _powershell()
    if shell is None:
        pytest.skip("PowerShell is not installed in this environment.")

    script = textwrap.dedent(
        f"""
        . '{INSTALL_PS1}' -SkipMain
        Write-OpenSreHeader -Channel release -RequestedVersion '' -InstallDir 'C:\\opensre' -Repo 'Tracer-Cloud/opensre'
        Invoke-OpenSreStep -Name 'Unit progress step' -Operation {{ 'result-value' }}
        Write-OpenSreProgressLine -Label 'opensre_main_windows-arm64.zip.sha256' -DownloadedBytes 10 -TotalBytes 100
        Clear-OpenSreProgressLine
        """
    )

    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    assert "OpenSRE installer" in output
    assert "Unit progress step" in output
    assert "OK Unit progress step" in output
    assert "result-value" in output


def test_install_ps1_strips_the_exe_suffix_without_eating_the_name() -> None:
    """The Next steps block must say `opensre`, not `opensr`.

    `$binaryName.TrimEnd(".exe")` reads as "drop the extension" and is not:
    TrimEnd takes a *set of characters*, so it also removes the trailing "e"
    of "opensre". The installer then told the user to run a command that does
    not exist.
    """
    source = INSTALL_PS1.read_text()

    assert '.TrimEnd(".exe")' not in source, (
        "TrimEnd with a suffix-shaped argument is back; it strips characters, "
        "not a suffix"
    )
    assert "[System.IO.Path]::GetFileNameWithoutExtension($binaryName)" in source


def test_install_ps1_next_steps_name_survives_powershell() -> None:
    """Measured in a real shell, because this bug is a PowerShell semantic."""
    shell = _powershell()
    if shell is None:
        pytest.skip("PowerShell is not installed in this environment.")

    script = textwrap.dedent(
        """
        $binaryName = 'opensre.exe'
        Write-Output ("trimend=" + $binaryName.TrimEnd(".exe"))
        Write-Output ("fixed=" + [System.IO.Path]::GetFileNameWithoutExtension($binaryName))
        Write-Output ("bare=" + [System.IO.Path]::GetFileNameWithoutExtension('opensre'))
        """
    )

    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    output = result.stdout
    # The old expression really does produce the wrong name, on this shell.
    assert "trimend=opensr\n" in output.replace("\r", "")
    assert "fixed=opensre" in output
    # And the replacement is a no-op when the archive ships no .exe suffix.
    assert "bare=opensre" in output
