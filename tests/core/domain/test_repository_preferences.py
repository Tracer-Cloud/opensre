"""Organization-wide repository preferences."""

from __future__ import annotations

from core.domain.repositories import (
    exclude_repository,
    load_repository_preferences,
    preferred_repository_for_name,
    set_primary_repository,
)


def test_excluding_a_primary_repository_removes_it_from_primary() -> None:
    """A repository is never both the one to fix and one never to change."""
    set_primary_repository("https://github.com/Tracer-Cloud/opensre.git", note="fix CI only")
    exclude_repository("Tracer-Cloud/opensre")

    preferences = load_repository_preferences()

    assert preferences.primary == ()
    assert preferences.excluded == ("Tracer-Cloud/opensre",)
    assert preferences.notes == {"Tracer-Cloud/opensre": "fix CI only"}


def test_a_longer_name_does_not_match_a_shorter_primary() -> None:
    set_primary_repository("Tracer-Cloud/opensre")

    assert preferred_repository_for_name("deploy opensre-webapp") is None
    assert preferred_repository_for_name("look at someone/opensre") is None
    assert preferred_repository_for_name("Fix OpenSRE tests") == "Tracer-Cloud/opensre"
