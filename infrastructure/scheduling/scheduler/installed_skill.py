"""Skills installed as plain folders, such as one ``clawhub install`` wrote, for agent loops.

An installed skill lives outside OpenSRE's signed catalog: a folder holding a
``SKILL.md`` whose optional YAML frontmatter names it, in the shared Agent
Skills format. A loop stores the folder's absolute path and reads the file again
on every tick, so updating the installed copy updates the loop.
"""

from __future__ import annotations

from pathlib import Path

import yaml

INSTALLED_SKILL_FILE = "SKILL.md"
#: Largest SKILL.md a tick puts in its prompt; a padded or runaway file fails the tick.
INSTALLED_SKILL_MAX_BYTES = 64 * 1024


def is_installed_skill_path(skill: str) -> bool:
    """Whether ``skill`` is a filesystem path; catalog card names never hold a separator."""
    value = skill.strip()
    return value.startswith(("/", "~", ".")) or "/" in value or "\\" in value


def installed_skill_folder(skill: str) -> Path:
    """The absolute folder of the skill at ``skill``, given its folder or its SKILL.md."""
    path = Path(skill.strip()).expanduser()
    if path.name == INSTALLED_SKILL_FILE:
        path = path.parent
    return path.resolve()


def read_installed_skill(skill: str) -> tuple[str, str]:
    """Return the name and Markdown body of the installed skill at ``skill``.

    The name comes from the frontmatter, else the folder name. Raises
    ``RuntimeError`` when the file is missing, unreadable, too large or empty,
    so a tick fails instead of running without its instructions.
    """
    folder = installed_skill_folder(skill)
    card = folder / INSTALLED_SKILL_FILE
    try:
        if card.stat().st_size > INSTALLED_SKILL_MAX_BYTES:
            raise RuntimeError(
                f"Loop skill {str(folder)!r} is larger than {INSTALLED_SKILL_MAX_BYTES} bytes."
            )
        text = card.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"Loop skill {str(folder)!r} is not installed.") from exc
    name, body = _split_card(text)
    if not body:
        raise RuntimeError(f"Loop skill {str(folder)!r} has no instructions.")
    return name or folder.name, body


def _split_card(text: str) -> tuple[str, str]:
    """The frontmatter ``name`` ("" when absent) and the body of a SKILL.md."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return "", text.strip()
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        return "", text.strip()
    try:
        meta = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError:
        meta = None
    name = meta.get("name") if isinstance(meta, dict) else None
    body = "\n".join(lines[end + 1 :]).strip()
    return (name.strip() if isinstance(name, str) else ""), body


__all__ = [
    "INSTALLED_SKILL_FILE",
    "INSTALLED_SKILL_MAX_BYTES",
    "installed_skill_folder",
    "is_installed_skill_path",
    "read_installed_skill",
]
