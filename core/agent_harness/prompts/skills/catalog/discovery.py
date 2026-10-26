"""Discover workflow card paths in stable package and file order."""

from __future__ import annotations

from pathlib import Path

from config.constants.skills import SKILL_FILENAME, SKILL_REPORT_SUFFIX


def _package_skill_path(package_dir: Path) -> Path | None:
    """Return the skill recipe path inside a package directory, if present."""
    for candidate in (
        package_dir / SKILL_FILENAME,
        package_dir / f"{package_dir.name}.md",
    ):
        if candidate.is_file():
            return candidate
    return None


def iter_skill_paths(directory: Path) -> list[Path]:
    """Return skill recipe paths in stable order (packages, nested packages, flat files).

    A package directory may nest one level of child skill packages (e.g.
    ``onboarding-github-ci/a-analyzing-github-ci-performance/SKILL.md``); each child follows its
    parent so related skills stay adjacent in the index.
    """
    paths: list[Path] = []
    for child in sorted(directory.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        skill_file = _package_skill_path(child)
        if skill_file is not None:
            paths.append(skill_file)
        for nested in sorted(child.iterdir()):
            if not nested.is_dir() or nested.name.startswith("."):
                continue
            nested_file = _package_skill_path(nested)
            if nested_file is not None:
                paths.append(nested_file)
    paths.extend(
        path
        for path in sorted(directory.glob("*.md"))
        if path.name not in {"AGENTS.md", "README.md"}
        and not path.name.endswith(SKILL_REPORT_SUFFIX)
    )
    return paths
