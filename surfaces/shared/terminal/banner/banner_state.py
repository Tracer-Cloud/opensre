"""Offline status probes for the compact launch banner.

Counts only — no skill bodies, no vendor SDKs, no prompt_toolkit. The chips are
decorative startup chrome; loading the action-skill harness or full catalog
health graph here made first paint pay hundreds of milliseconds for two integers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from config.constants.skills import SKILLS_DIR_ENV
from integrations.github import count_ci_fixes


@dataclass(frozen=True)
class LaunchStatus:
    """Counts displayed beside the OpenSRE mark."""

    skill_count: int
    ci_fix_count: int


# ``surfaces/shared/terminal/banner/banner_state.py`` → package root (opensre/).
_PACKAGE_ROOT = Path(__file__).resolve().parents[4]
_BUNDLED_SKILLS_DIR = _PACKAGE_ROOT / "core" / "agent_harness" / "prompts" / "skills"


_NOT_SKILLS = frozenset({"AGENTS.md", "README.md"})
_REPORT_SUFFIX = "_report.md"


def _has_card(directory: Path) -> bool:
    return (directory / "SKILL.md").is_file() or (directory / f"{directory.name}.md").is_file()


def _count_bundled_skill_files(directory: Path) -> int:
    """Count discoverable skill recipes without importing the skill loader.

    Mirrors ``catalog.discovery.iter_skill_paths``: package dirs, one level of
    nested child packages, then top-level ``*.md`` cards — without reading file
    bodies or importing harness package ``__init__`` graphs.
    """
    if not directory.is_dir():
        return 0
    count = 0
    for child in sorted(directory.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        count += _has_card(child)
        count += sum(
            1
            for nested in child.iterdir()
            if nested.is_dir() and not nested.name.startswith(".") and _has_card(nested)
        )
    count += sum(
        1
        for path in directory.glob("*.md")
        if path.name not in _NOT_SKILLS and not path.name.endswith(_REPORT_SUFFIX)
    )
    return count


def _skills_dir() -> Path:
    """The catalog this process serves: ``OPENSRE_SKILLS_DIR`` when set, else the bundle."""
    override = os.getenv(SKILLS_DIR_ENV, "").strip()
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_dir():
            return candidate
    return _BUNDLED_SKILLS_DIR


def _count_loaded_skills() -> int:
    """Return the number of action-agent skills (filesystem only)."""
    try:
        return _count_bundled_skill_files(_skills_dir())
    except Exception:
        return 0


def load_launch_status() -> LaunchStatus:
    """Load the startup-safe status summary without network calls."""
    return LaunchStatus(
        skill_count=_count_loaded_skills(),
        ci_fix_count=count_ci_fixes(),
    )


__all__ = ["LaunchStatus", "load_launch_status"]
