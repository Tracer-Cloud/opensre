"""Carry a turn's organization scope into a CLI child process the turn starts.

The scope is a ``ContextVar`` (:mod:`config.scope_context`) and a child process
inherits only the environment, so the parent writes the bound scope there with
:func:`hand_off_scope` and the child reads it back with :func:`handed_off_scope`.
:func:`acting_scope` answers for either side: the bound turn's scope, else the
one handed to this process. A chat user controls a slash command's arguments,
never its environment, so no command line can claim another member or
organization.

Leaf module: depends only on :mod:`config`, so any layer can import it.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping

from config.constants.organization import organization_id
from config.constants.tenancy import TURN_ACTOR_ID_ENV, TURN_ORGANIZATION_ID_ENV
from config.principal import Actor, Principal, PrincipalKind, StorageScope
from config.scope_context import current_scope


def hand_off_scope(env: MutableMapping[str, str]) -> None:
    """Name the bound organization scope in a child's ``env``, replacing any inherited claim."""
    env.pop(TURN_ORGANIZATION_ID_ENV, None)
    env.pop(TURN_ACTOR_ID_ENV, None)
    scope = current_scope()
    if scope is None or scope.principal.kind != PrincipalKind.ORG:
        return
    env[TURN_ORGANIZATION_ID_ENV] = scope.principal.id
    env[TURN_ACTOR_ID_ENV] = scope.actor.id


def handed_off_scope() -> StorageScope | None:
    """The organization scope of the turn that started this process, or ``None``.

    On a deployment that declares its organization, a hand-off naming any other
    organization is ignored.
    """
    organization = (os.getenv(TURN_ORGANIZATION_ID_ENV) or "").strip()
    actor = (os.getenv(TURN_ACTOR_ID_ENV) or "").strip()
    if not organization or not actor:
        return None
    served = organization_id()
    if served and organization != served:
        return None
    return StorageScope(principal=Principal.org(organization), actor=Actor(id=actor))


def acting_scope() -> StorageScope | None:
    """The organization scope this work runs under: the bound turn's, else the handed-off one."""
    scope = current_scope()
    if scope is not None:
        return scope if scope.principal.kind == PrincipalKind.ORG else None
    return handed_off_scope()


__all__ = ["acting_scope", "hand_off_scope", "handed_off_scope"]
