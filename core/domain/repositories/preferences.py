"""Which repositories OpenSRE works on, and which it must leave alone.

Session repository context forgets everything when the conversation ends, and
personal memories belong to one member. These preferences belong to the
organization: one file under :func:`config.constants.paths.deployment_home`,
which on a hosted gateway is the organization's mounted volume and on a laptop
is ``~/.opensre``. Every member, the shell and scheduled runs read the same
file, and it survives a gateway rollout.

``primary`` repositories win whenever a request names a repository only by its
short name ("fix the opensre repo") or names none, so a fork or a mirror with
the same name is never chosen over the organization's own repository.
``excluded`` repositories are never changed: no branches, pushes, pull
requests or issues.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.constants.paths import ContextRootOwnerMismatchError, deployment_home

PREFERENCES_FILENAME = "repositories.json"
_MAX_REPOSITORIES = 50
_MAX_NOTE_CHARS = 300
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*$")


@dataclass(frozen=True)
class RepositoryPreferences:
    """The organization's repository choices, as stored."""

    primary: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
    notes: dict[str, str] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (self.primary or self.excluded or self.notes)


def repository_preferences_path() -> Path:
    return deployment_home() / PREFERENCES_FILENAME


def normalize_repository(value: str) -> str | None:
    """``owner/repo`` from a full name or GitHub URL, or ``None`` when it is not one."""
    cleaned = value.strip().removesuffix("/").removesuffix(".git")
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    cleaned = cleaned.strip("/")
    return cleaned if _REPOSITORY_RE.match(cleaned) else None


def _repositories(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    found: list[str] = []
    for item in value:
        repository = normalize_repository(item) if isinstance(item, str) else None
        if repository and repository.lower() not in {seen.lower() for seen in found}:
            found.append(repository)
    return tuple(found[:_MAX_REPOSITORIES])


def load_repository_preferences() -> RepositoryPreferences:
    """The stored preferences; empty when the file is missing or unreadable.

    Read at the start of every turn, so a mount that refuses this organization
    (``ContextRootOwnerMismatchError``) reads as empty rather than failing it.
    """
    try:
        loaded = json.loads(repository_preferences_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, ContextRootOwnerMismatchError):
        return RepositoryPreferences()
    if not isinstance(loaded, dict):
        return RepositoryPreferences()
    raw_notes = loaded.get("notes")
    notes = {
        repository: note.strip()[:_MAX_NOTE_CHARS]
        for key, note in (raw_notes.items() if isinstance(raw_notes, dict) else ())
        if isinstance(key, str)
        and isinstance(note, str)
        and note.strip()
        and (repository := normalize_repository(key))
    }
    return RepositoryPreferences(
        primary=_repositories(loaded.get("primary")),
        excluded=_repositories(loaded.get("excluded")),
        notes=notes,
    )


def _save(preferences: RepositoryPreferences) -> None:
    """Write atomically, so a reader never sees half a file. May raise ``OSError``."""
    path = repository_preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "primary": list(preferences.primary),
        "excluded": list(preferences.excluded),
        "notes": dict(sorted(preferences.notes.items())),
    }
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".repositories-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _without(repositories: tuple[str, ...], repository: str) -> tuple[str, ...]:
    return tuple(item for item in repositories if item.lower() != repository.lower())


def _with_note(notes: dict[str, str], repository: str, note: str | None) -> dict[str, str]:
    """``notes`` with ``repository``'s note replaced; an empty note keeps the old one."""
    if not (note and note.strip()):
        return dict(notes)
    updated = {key: value for key, value in notes.items() if key.lower() != repository.lower()}
    updated[repository] = note.strip()[:_MAX_NOTE_CHARS]
    return updated


def set_primary_repository(repository: str, note: str | None = None) -> RepositoryPreferences:
    """Make ``repository`` the one to use for its name, first among the primaries.

    A primary repository is never excluded, so this also lifts an exclusion.
    Raises ``ValueError`` for anything that is not ``owner/repo``.
    """
    normalized = normalize_repository(repository)
    if normalized is None:
        raise ValueError(f"not an owner/repo repository name: {repository!r}")
    current = load_repository_preferences()
    updated = RepositoryPreferences(
        primary=(normalized, *_without(current.primary, normalized))[:_MAX_REPOSITORIES],
        excluded=_without(current.excluded, normalized),
        notes=_with_note(current.notes, normalized, note),
    )
    _save(updated)
    return updated


def exclude_repository(repository: str, note: str | None = None) -> RepositoryPreferences:
    """Never change ``repository``. Raises ``ValueError`` for a malformed name."""
    normalized = normalize_repository(repository)
    if normalized is None:
        raise ValueError(f"not an owner/repo repository name: {repository!r}")
    current = load_repository_preferences()
    updated = RepositoryPreferences(
        primary=_without(current.primary, normalized),
        excluded=(*_without(current.excluded, normalized), normalized)[-_MAX_REPOSITORIES:],
        notes=_with_note(current.notes, normalized, note),
    )
    _save(updated)
    return updated


def forget_repository(repository: str) -> RepositoryPreferences:
    """Drop every preference about ``repository``."""
    normalized = normalize_repository(repository)
    if normalized is None:
        raise ValueError(f"not an owner/repo repository name: {repository!r}")
    current = load_repository_preferences()
    updated = RepositoryPreferences(
        primary=_without(current.primary, normalized),
        excluded=_without(current.excluded, normalized),
        notes={k: v for k, v in current.notes.items() if k.lower() != normalized.lower()},
    )
    _save(updated)
    return updated


def preferred_repository_for_name(
    text: str, preferences: RepositoryPreferences | None = None
) -> str | None:
    """The primary repository a message names only by its short name, if any.

    "fix the failing tests in opensre" resolves to ``Tracer-Cloud/opensre`` when
    that is a primary repository, whatever forks of ``opensre`` exist. The
    first primary listed wins when two share a name.
    """
    prefs = preferences if preferences is not None else load_repository_preferences()
    lowered = text.lower()
    for repository in prefs.primary:
        name = repository.split("/", 1)[1].lower()
        if re.search(rf"(?<![\w./-]){re.escape(name)}(?![\w/-])", lowered):
            return repository
    return None


__all__ = [
    "PREFERENCES_FILENAME",
    "RepositoryPreferences",
    "exclude_repository",
    "forget_repository",
    "load_repository_preferences",
    "normalize_repository",
    "preferred_repository_for_name",
    "repository_preferences_path",
    "set_primary_repository",
]
