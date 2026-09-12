"""Offline status probes for the compact launch banner.

Counts only — no skill bodies, no vendor SDKs, no prompt_toolkit. The chips are
decorative startup chrome; loading the action-skill harness or full catalog
health graph here made first paint pay hundreds of milliseconds for two integers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from integrations.github import count_ci_fixes


@dataclass(frozen=True)
class LaunchStatus:
    """Counts displayed beside the OpenSRE mark."""

    skill_count: int
    ci_fix_count: int


# ``surfaces/shared/terminal/banner/banner_state.py`` → package root (opensre/).
_PACKAGE_ROOT = Path(__file__).resolve().parents[4]
_BUNDLED_SKILLS_DIR = _PACKAGE_ROOT / "core" / "agent_harness" / "prompts" / "skills"


def _count_bundled_skill_files(directory: Path) -> int:
    """Count discoverable skill recipes without importing the skill loader.

    Mirrors ``loader._iter_skill_paths`` (package dirs then top-level ``*.md``)
    and dedupes by skill name the way ``list_action_skills`` does — without
    reading file bodies or importing harness package ``__init__`` graphs.
    """
    if not directory.is_dir():
        return 0
    names: set[str] = set()

    def _add(name: str) -> None:
        key = name.replace("_", "-").lower()
        if key:
            names.add(key)

    for child in sorted(directory.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / "SKILL.md").is_file() or (child / f"{child.name}.md").is_file():
            _add(child.name)
    for path in sorted(directory.glob("*.md")):
        _add(path.stem)
    return len(names)


def _count_loaded_skills() -> int:
    """Return the number of bundled action-agent skills (filesystem only)."""
    try:
        return _count_bundled_skill_files(_BUNDLED_SKILLS_DIR)
    except Exception:
        return 0


def load_launch_status() -> LaunchStatus:
    """Load the startup-safe status summary without network calls."""
    return LaunchStatus(
        skill_count=_count_loaded_skills(),
        ci_fix_count=count_ci_fixes(),
    )


__all__ = ["LaunchStatus", "load_launch_status"]
