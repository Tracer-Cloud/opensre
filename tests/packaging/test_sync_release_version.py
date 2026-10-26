from __future__ import annotations

import subprocess
import sys

from config.constants.paths import REPO_ROOT
from infrastructure.deployment.packaging.sync_release_version import canonical_release_version

_SCRIPT = REPO_ROOT / "infrastructure" / "deployment" / "packaging" / "sync_release_version.py"


def test_canonical_release_version_strips_leading_zeros_on_numeric_sha() -> None:
    """PEP 440 local numeric segments drop leading zeros; match hatchling metadata."""
    from packaging.version import Version

    numeric_sha = "0.1.2026.8.31+main.0273802"
    assert canonical_release_version(numeric_sha) == str(Version(numeric_sha))
    assert canonical_release_version(numeric_sha) == "0.1.2026.8.31+main.273802"
    hex_sha = "0.1.2026.8.31+main.0c306ad"
    assert canonical_release_version(hex_sha) == hex_sha
    assert canonical_release_version("v0.1.2026.8.31") == "0.1.2026.8.31"


def test_sync_release_version_updates_pyproject() -> None:
    before = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    try:
        subprocess.run(
            [sys.executable, str(_SCRIPT), "--version", "0.0.test-sync"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert 'version = "0.0.test-sync"' in (REPO_ROOT / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    finally:
        (REPO_ROOT / "pyproject.toml").write_text(before, encoding="utf-8")
