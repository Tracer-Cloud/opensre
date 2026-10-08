"""Spot ``owner/repo`` repository identifiers in free text."""

from __future__ import annotations

import re

#: ``owner/repo`` as written in prose or a heading, not part of a path or URL:
#: nothing path- or URL-like right before it, and no word, slash or dash right after.
REPOSITORY_ID_RE = re.compile(
    r"(?<![\w./:@-])([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100})(?![\w/-])"
)


def repository_ids(text: str) -> list[tuple[str, str]]:
    """Every ``(owner, repo)`` in ``text``, in order, with a sentence's trailing dot removed."""
    found: list[tuple[str, str]] = []
    for owner, name in REPOSITORY_ID_RE.findall(text):
        name = name.rstrip(".")
        if name:
            found.append((owner, name))
    return found


__all__ = ["REPOSITORY_ID_RE", "repository_ids"]
