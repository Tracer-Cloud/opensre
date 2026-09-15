"""Shared assertions for the live installer canaries (POSIX + Windows)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path


def assert_checksum_verified(installer_output: str) -> None:
    """Require the installer output to prove checksum verification completed."""
    normalized_output = installer_output.casefold()
    assert "verifying checksum" in normalized_output, installer_output
    assert "missing checksum asset" not in normalized_output, installer_output


def assert_command_smoke(
    command: Sequence[str | os.PathLike[str]],
    *,
    help_flag: str,
    requested_tag: str,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Assert version, help, and package-smoke behavior through one command."""
    argv = [os.fspath(part) for part in command]
    version = subprocess.run(
        [*argv, "--version"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert version.returncode == 0, version.stdout + version.stderr
    if requested_tag:
        assert requested_tag.removeprefix("v") in version.stdout, version.stdout

    help_result = subprocess.run(
        [*argv, help_flag],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert help_result.returncode == 0, help_result.stdout + help_result.stderr

    smoke = subprocess.run(
        [*argv, "_package-smoke"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert smoke.returncode == 0, smoke.stdout + smoke.stderr


def assert_binary_smoke(
    binary: Path,
    *,
    help_flag: str,
    requested_tag: str,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Assert smoke behavior by invoking an executable directly."""
    assert_command_smoke(
        [binary],
        help_flag=help_flag,
        requested_tag=requested_tag,
        cwd=cwd,
        env=env,
    )
