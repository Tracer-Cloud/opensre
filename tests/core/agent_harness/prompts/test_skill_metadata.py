"""Every skill card carries the same metadata block, so people and docs can rely on it."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from core.agent_harness.prompts.skills.loader import (
    _parse_frontmatter,
    _resolve_skill_reference,
    skills_dir,
)

_REQUIRED = (
    "owner",
    "last_changed_by",
    "last_changed_at",
    "usecases",
    "requires",
    "type",
    "version",
)
_TYPES = {"onboarding", "analytics", "report", "repair", "audit"}
# ``owner`` names the person who created the skill; team labels hide that.
_TEAM_LABEL = "team"


def _skill_cards() -> list[Path]:
    return sorted(Path(skills_dir()).rglob("SKILL.md"))


@pytest.mark.parametrize("card", _skill_cards(), ids=lambda path: path.parent.name)
def test_every_skill_card_has_the_metadata_block(card: Path) -> None:
    # Arrange
    frontmatter, _body = _parse_frontmatter(card.read_text(encoding="utf-8"))

    # Act
    metadata = frontmatter.get("metadata")

    # Assert: the block exists, every key is filled, and the type is a known kind.
    assert isinstance(metadata, dict), f"{card.parent.name}: missing metadata block"
    for key in _REQUIRED:
        assert metadata.get(key), f"{card.parent.name}: metadata.{key} is empty"
    assert isinstance(metadata["usecases"], list) and len(metadata["usecases"]) >= 1
    assert isinstance(metadata["requires"], list) and len(metadata["requires"]) >= 1
    assert metadata["type"] in _TYPES, f"{card.parent.name}: unknown type {metadata['type']!r}"
    for key in ("owner", "last_changed_by"):
        value = metadata[key]
        assert isinstance(value, str), f"{card.parent.name}: metadata.{key} must be a name"
        assert _TEAM_LABEL not in value.lower(), (
            f"{card.parent.name}: metadata.{key} must name a person, not a team ({value!r})"
        )
    # Unquoted ``YYYY-MM-DD`` parses as a date; a quoted or malformed value is a string.
    changed_at = metadata["last_changed_at"]
    assert isinstance(changed_at, date), (
        f"{card.parent.name}: metadata.last_changed_at must be an unquoted ISO date "
        f"(YYYY-MM-DD), got {changed_at!r}"
    )
    # Editors can already be on tomorrow's UTC date, as far ahead as UTC+14.
    latest_calendar_date = (datetime.now(UTC) + timedelta(hours=14)).date()
    assert changed_at <= latest_calendar_date, (
        f"{card.parent.name}: metadata.last_changed_at {changed_at} is in the future"
    )


def test_declared_references_resolve_inside_the_skills_tree() -> None:
    missing: list[str] = []
    for card in _skill_cards():
        frontmatter, _body = _parse_frontmatter(card.read_text(encoding="utf-8"))
        references = frontmatter.get("references")
        if not references:
            continue
        assert isinstance(references, list), f"{card.parent.name}: references must be a list"
        for ref in references:
            if not isinstance(ref, str) or not ref.strip():
                missing.append(f"{card.parent.name}: empty reference")
                continue
            resolved = _resolve_skill_reference(card, ref)
            if resolved is None or not resolved.is_file():
                missing.append(f"{card.parent.name}: {ref!r}")
    assert missing == []
