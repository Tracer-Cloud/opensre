"""Who may read workspace knowledge: full members of the organization, and nobody else.

Workspace knowledge is built from what every full member can see (public
channels, the member directory). A Slack guest, or a Slack Connect user from
another organization, cannot see those, so their turns must not read it
either, even where a gateway admits them (an open workspace).

The rule, for a turn bound to an organization:

- a Slack user may read it only if the last Slack scan listed them as a full
  member (``.readers.json`` beside the entries, written by the scan);
- a member acting from the OpenSRE app (Clerk user id) or through the
  organization's own control plane may read it: the app admits members only;
- anyone else may not. Before any scan has run, no Slack user may.

A turn bound to no organization (a laptop) is the operator's own and always
may. The list is re-read when the file changes, so a member removed in Slack
loses access at the next scan.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from filelock import Timeout

from config.constants.gateway import PROMPT_DEFAULT_ACTOR
from config.principal import PrincipalKind
from config.scope_context import current_scope
from core.domain.knowledge.store import knowledge_dir, workspace_store
from core.domain.memory.files import memory_lock, read_json_object, write_json_atomically

READERS_FILENAME = ".readers.json"
_CLERK_USER = re.compile(r"^user_[A-Za-z0-9]{10,64}$")
#: Actors the organization's own control plane uses for app turns.
_APP_ACTORS: frozenset[str] = frozenset({PROMPT_DEFAULT_ACTOR})


def _readers_path(directory: Path) -> Path:
    return directory / READERS_FILENAME


def record_workspace_readers(
    service: str, member_ids: Iterable[str], *, merge: bool = False
) -> bool:
    """Record the full members ``service``'s scan saw; ``False`` when it cannot be saved.

    The list replaces the previous one, or with ``merge`` (a partial list)
    adds to it.
    """
    try:
        with workspace_store() as directory, memory_lock():
            path = _readers_path(directory)
            readers = read_json_object(path)
            seen = {str(member) for member in member_ids if member}
            previous = readers.get(service)
            if merge and isinstance(previous, list):
                seen |= {str(member) for member in previous}
            readers[service] = sorted(seen)
            write_json_atomically(path, readers)
    except (Timeout, OSError):
        return False
    return True


@lru_cache(maxsize=8)
def _readers(path_key: str, _signature: tuple[int, int]) -> frozenset[str]:
    readers = read_json_object(Path(path_key))
    return frozenset(
        str(member)
        for members in readers.values()
        if isinstance(members, list)
        for member in members
    )


def workspace_readers() -> frozenset[str]:
    """Every member id any scan recorded as a full member; empty before the first scan."""
    path = _readers_path(knowledge_dir())
    try:
        stat = path.stat()
    except OSError:
        return frozenset()
    return _readers(str(path), (stat.st_mtime_ns, stat.st_size))


def caller_may_read_workspace() -> bool:
    """Whether the actor taking this turn may read the organization's workspace knowledge."""
    scope = current_scope()
    if scope is None or scope.principal.kind != PrincipalKind.ORG:
        return True
    actor = scope.actor.id
    if _CLERK_USER.match(actor) or actor in _APP_ACTORS:
        return True
    return actor in workspace_readers()


__all__ = [
    "READERS_FILENAME",
    "caller_may_read_workspace",
    "record_workspace_readers",
    "workspace_readers",
]
