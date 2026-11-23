"""The OpenObserve organization as one segment of the search request path.

Anything else in it (``/``, ``?``, ``..``) would send the stored credentials to
a different endpoint than the search the tool is for.
"""

from __future__ import annotations

import re

_ORGANIZATION_NAME = re.compile(r"[A-Za-z0-9_-]+")


def is_organization_name(name: str) -> bool:
    """Whether ``name`` is a plain organization name that is safe as a path segment."""
    return bool(_ORGANIZATION_NAME.fullmatch(name))


__all__ = ["is_organization_name"]
