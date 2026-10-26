"""Save which repositories the organization works on and which it leaves alone.

A thin wrapper over :mod:`core.domain.repositories`. The choices are shared by
every member, the shell and scheduled runs, and outlive the session.
"""

from __future__ import annotations

from typing import Any

from core.domain.repositories import (
    RepositoryPreferences,
    exclude_repository,
    forget_repository,
    load_repository_preferences,
    set_primary_repository,
)
from core.domain.types.tools import ToolRole, ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool

_ACTIONS = ("list", "set_primary", "exclude", "forget")


def _result(preferences: RepositoryPreferences, **extra: Any) -> dict[str, Any]:
    return {
        "primary": list(preferences.primary),
        "never_change": list(preferences.excluded),
        "notes": dict(preferences.notes),
        **extra,
    }


@tool(
    name="repository_preferences",
    display_name="Repository preferences",
    source="knowledge",
    description=(
        "Read or save the organization's standing repository choices. 'set_primary' "
        "makes owner/repo the repository a short name like 'opensre' means, ahead of "
        "its forks and mirrors. 'exclude' marks owner/repo as never to be changed (no "
        "branches, pushes, pull requests or issues). 'forget' drops both. 'list' "
        "returns them. Save in the turn the user says which repository to work on or "
        "to leave alone; every member and later session sees the same choices."
    ),
    use_cases=[
        "The user says 'always fix Tracer-Cloud/opensre, not the forks'",
        "The user says 'never touch our fork' or 'leave the mirror alone'",
        "Several repositories share a name and the user picks one",
    ],
    tags=("safe", "fast", "no-credentials"),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    role=ToolRole.BOOKKEEPING,
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(_ACTIONS),
                "description": "What to do: read the choices, or save, exclude or drop one.",
            },
            "repository": {
                "type": "string",
                "description": "owner/repo or a github.com URL. Required except for 'list'.",
            },
            "note": {
                "type": "string",
                "description": (
                    "What to fix or leave alone in this repository, in the user's words "
                    "(max 300 chars). Optional."
                ),
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    },
)
def repository_preferences(
    action: str,
    repository: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Apply one change to the organization's repository preferences."""
    if action == "list":
        return _result(load_repository_preferences())
    if action not in _ACTIONS:
        return {"error": "invalid_action", "detail": f"action must be one of {list(_ACTIONS)}"}
    if not repository or not repository.strip():
        return {"error": "missing_repository", "detail": f"'{action}' needs owner/repo"}
    try:
        if action == "set_primary":
            updated = set_primary_repository(repository, note)
        elif action == "exclude":
            updated = exclude_repository(repository, note)
        else:
            updated = forget_repository(repository)
    except ValueError as exc:
        return {"error": "invalid_repository", "detail": str(exc)}
    return _result(updated, saved=action)
