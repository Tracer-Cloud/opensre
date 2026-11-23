"""The Atlas project id as one segment of a request path.

Every Atlas request path is built from the project id. A ``/`` or ``?`` in it
would send the API key to a different Atlas resource than the tool is for.
"""

from __future__ import annotations

import re

_PROJECT_ID = re.compile(r"[A-Za-z0-9_-]+")


def validated_project_id(value: object) -> str:
    """Return the stripped project id, or raise when it is not a plain identifier."""
    project_id = str(value or "").strip()
    if project_id and not _PROJECT_ID.fullmatch(project_id):
        raise ValueError("Atlas project_id must contain only letters, digits, '_' or '-'.")
    return project_id


__all__ = ["validated_project_id"]
