"""Keep process inspection behind the shared runtime wrappers."""

from __future__ import annotations

import pathlib

from tests.shared.product_sources import product_python_files

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PSUTIL_SANCTIONED = (
    _REPO_ROOT / "config" / "runtime_metadata" / "probes.py",
    _REPO_ROOT / "infrastructure" / "process" / "termination.py",
    _REPO_ROOT / "infrastructure" / "process" / "tree.py",
)
_SOURCE_ROOTS = (
    "bootstrap",
    "config",
    "core",
    "gateway",
    "integrations",
    "infrastructure",
    "surfaces",
    "tools",
)


def test_psutil_is_not_imported_outside_sanctioned_modules() -> None:
    """``psutil`` must stay confined to the sanctioned wrapper modules so the
    dependency surface is explicit. A static scan over runtime package trees
    catches future regressions deterministically — runtime import-graph checks
    would be flaky against lazy-import patterns the codebase already uses
    elsewhere.
    """
    leaks: list[str] = []
    py_files: list[pathlib.Path] = []
    for root_name in _SOURCE_ROOTS:
        py_files.extend(product_python_files(_REPO_ROOT / root_name))
    for py_file in py_files:
        if py_file in _PSUTIL_SANCTIONED:
            continue
        text = py_file.read_text(encoding="utf-8")
        for needle in ("import psutil", "from psutil"):
            if needle in text:
                leaks.append(f"{py_file.relative_to(_REPO_ROOT)} contains {needle!r}")
                break

    assert not leaks, (
        "psutil leaked into modules other than the sanctioned wrappers "
        f"({', '.join(str(p.relative_to(_REPO_ROOT)) for p in _PSUTIL_SANCTIONED)}):\n  "
        + "\n  ".join(leaks)
    )
