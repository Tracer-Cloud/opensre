"""Who owns a scheduled loop, and whom a private loop's runs act as.

A loop belongs to the member who created it (``loop_created_by``): their Slack
user id for a loop made in Slack, their web app user id for one made in the
app or a web app session. A *private* loop is that member's own. Each run acts
as them, so it can use their personal accounts (Gmail, Drive, Outlook) and
their memory, and only they can see, change or run it. A shared loop runs with
the workspace's connections only and anyone in the organization may change it,
so no member can reach another's accounts through a loop they are able to edit.

One person acts under several ids (Slack in Slack, the web app id in the app).
The host installs a resolver from an id to the web app member it names
(:func:`install_member_resolver`) so an owner is recognized on every surface.
"""

from __future__ import annotations

from collections.abc import Callable

from config.constants.organization import organization_id
from config.principal import Actor, Principal, StorageScope
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_CREATED_BY_PARAM,
    LOOP_PRIVATE_PARAM,
    LOOP_SHELL_CREATOR,
    LOOP_WEBAPP_CREATOR,
)
from infrastructure.scheduling.scheduler.types import ScheduledTask

#: An actor id -> the web app user id of the member it names, or ``None``. Never raises.
MemberResolver = Callable[[str], str | None]

#: Creators that are processes, not members: their loops have no owner to act as.
_PROCESS_CREATORS = frozenset({LOOP_SHELL_CREATOR, LOOP_WEBAPP_CREATOR})

_resolver: MemberResolver | None = None


def install_member_resolver(resolver: MemberResolver | None) -> None:
    """Set how actor ids map to web app members; ``None`` recognizes an owner by exact id only."""
    global _resolver
    _resolver = resolver


def loop_owner(task: ScheduledTask) -> str:
    """The member who created ``task``'s loop, or "" when a process did."""
    creator = str(task.params.get(LOOP_CREATED_BY_PARAM) or "").strip()
    return "" if creator in _PROCESS_CREATORS else creator


def loop_is_private(task: ScheduledTask) -> bool:
    """Whether ``task``'s loop is its owner's own.

    Only an explicit ``loop_private`` of ``"true"`` makes it so, never where it
    delivers: a shared loop's destination may have been set by any member.
    """
    flag = str(task.params.get(LOOP_PRIVATE_PARAM) or "").strip().lower()
    return flag == "true" and bool(loop_owner(task))


def same_member(first: str, second: str) -> bool:
    """Whether two actor ids name one person: the same id, or the same web app member."""
    first, second = first.strip(), second.strip()
    if not first or not second:
        return False
    if first == second:
        return True
    resolver = _resolver
    if resolver is None:
        return False
    member = resolver(first)
    return member is not None and member == resolver(second)


def owned_by(task: ScheduledTask, actor: str) -> bool:
    """Whether ``actor`` is the member who owns ``task``'s loop."""
    owner = loop_owner(task)
    return bool(owner) and same_member(actor, owner)


def unattended_scope(task: ScheduledTask) -> StorageScope | None:
    """The scope a run of ``task`` takes when no member's turn is running it.

    A private loop acts as its owner. A shared loop gets ``None`` and runs with
    the workspace's connections only, as every loop did before private ones.
    """
    if not loop_is_private(task):
        return None
    organization = task.organization or organization_id()
    if not organization:
        return None
    return StorageScope(principal=Principal.org(organization), actor=Actor(id=loop_owner(task)))


__all__ = [
    "MemberResolver",
    "install_member_resolver",
    "loop_is_private",
    "loop_owner",
    "owned_by",
    "same_member",
    "unattended_scope",
]
