"""Organization-wide repository preferences: which repos to work on, which to leave alone."""

from __future__ import annotations

from core.domain.repositories.preferences import (
    RepositoryPreferences,
    exclude_repository,
    forget_repository,
    load_repository_preferences,
    preferred_repository_for_name,
    repository_preferences_path,
    set_primary_repository,
)

__all__ = [
    "RepositoryPreferences",
    "exclude_repository",
    "forget_repository",
    "load_repository_preferences",
    "preferred_repository_for_name",
    "repository_preferences_path",
    "set_primary_repository",
]
