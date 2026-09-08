"""Every skill card carries the same metadata block, so people and docs can rely on it."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.agent_harness.prompts.skills.loader import _parse_frontmatter, skills_dir

_REQUIRED = ("owner", "usecases", "requires", "type", "version")
_TYPES = {"onboarding", "analytics", "report", "repair", "audit"}


def _skill_cards() -> list[Path]:
    return sorted(Path(skills_dir()).glob("*/SKILL.md"))


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
    assert metadata["owner"] == "Tracer Team"
