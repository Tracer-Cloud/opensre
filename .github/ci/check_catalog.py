"""Quality commands shared by local validation and GitHub Actions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    """One isolated Python process in a quality group."""

    name: str
    group: str
    args: tuple[str, ...]


def quality_checks() -> tuple[Check, ...]:
    """Return the mandatory checks, including contracts spanning package boundaries."""
    sources = (
        "bootstrap",
        "config",
        "core",
        "gateway",
        "integrations",
        "infrastructure",
        "surfaces",
        "tools",
    )
    lint_paths = (*sources, "tests", ".github/ci")
    return (
        Check("lint", "static", ("-m", "ruff", "check", *lint_paths)),
        Check("format", "static", ("-m", "ruff", "format", "--check", *lint_paths)),
        Check("imports", "static", (".github/ci/check_imports.py", "--strict")),
        Check(
            "integration-registry",
            "static",
            (
                "-m",
                "pytest",
                "-q",
                "tests/integrations/test_verification_registry.py",
                "tests/integrations/test_registry.py",
            ),
        ),
        Check(
            "tool-registry", "static", ("-m", "pytest", "-q", "tests/tools/test_registry_index.py")
        ),
        Check(
            "global-contracts",
            "static",
            (
                "-m",
                "pytest",
                "-q",
                "tests/core/agent/test_tool_registry.py",
                "tests/fleet_monitoring/test_probe.py::test_psutil_is_not_imported_outside_sanctioned_modules",
                "tests/shared",
                "tests/quality",
            ),
        ),
        Check("types", "types", ("-m", "mypy", *sources, ".github/ci")),
        Check(
            "tool-contracts", "types", ("-m", "pytest", "-q", "tests/core/tool/test_contracts.py")
        ),
    )
