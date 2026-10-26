"""Shared assertions for the live installer canaries (POSIX + Windows)."""

from __future__ import annotations

import subprocess
from pathlib import Path


def assert_checksum_verified(installer_output: str) -> None:
    """Require the installer output to prove checksum verification completed."""
    normalized_output = installer_output.casefold()
    assert "verifying checksum" in normalized_output, installer_output
    assert "missing checksum asset" not in normalized_output, installer_output


def assert_binary_smoke(binary: Path, *, help_flag: str, requested_tag: str) -> None:
    """Assert ``--version`` (matches ``requested_tag`` if pinned), ``help_flag``, and ``_package-smoke`` all succeed."""
    version = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, timeout=30, check=False
    )
    assert version.returncode == 0, version.stderr
    if requested_tag:
        assert requested_tag.removeprefix("v") in version.stdout, version.stdout

    help_result = subprocess.run(
        [str(binary), help_flag], capture_output=True, text=True, timeout=30, check=False
    )
    assert help_result.returncode == 0, help_result.stdout + help_result.stderr

    smoke = subprocess.run(
        [str(binary), "_package-smoke"], capture_output=True, text=True, timeout=60, check=False
    )
    assert smoke.returncode == 0, smoke.stdout + smoke.stderr
