"""Ownership identities for a turn: principal, actor, and their pairing.

Leaf module: safe for any layer to import.

A :class:`Principal` owns credentials, integrations, and the bill. An
:class:`Actor` is the human taking a turn. In an org one principal has many
actors, so the two are separate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PrincipalKind(StrEnum):
    ORG = "org"
    INDIVIDUAL = "individual"


@dataclass(frozen=True)
class Principal:
    """Owner of credentials, integrations, and bill for one install context.

    ``kind="org"`` uses a Clerk (or equivalent) organization id.
    ``kind="individual"`` uses a Clerk user id for a personal context.
    """

    kind: PrincipalKind
    id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "kind",
            PrincipalKind(self.kind),
        )
        if not isinstance(self.id, str):
            raise TypeError("principal id must be a string")

        principal_id = self.id.strip()
        if not principal_id:
            raise ValueError("principal id must be non-empty")
        object.__setattr__(self, "id", principal_id)

    @classmethod
    def org(cls, org_id: str) -> Principal:
        """Build an organization principal."""
        return cls(kind=PrincipalKind.ORG, id=org_id)

    @classmethod
    def individual(cls, individual_id: str) -> Principal:
        """Build an individual principal."""
        return cls(kind=PrincipalKind.INDIVIDUAL, id=individual_id)


@dataclass(frozen=True)
class Actor:
    """The person taking a turn, within whatever principal owns the data."""

    id: str

    def __post_init__(self) -> None:
        actor_id = (self.id or "").strip()
        if not actor_id:
            raise ValueError("actor id must be non-empty")
        object.__setattr__(self, "id", actor_id)


@dataclass(frozen=True)
class StorageScope:
    """Who owns the data and who is reading it for one turn."""

    principal: Principal
    actor: Actor


__all__ = [
    "Actor",
    "Principal",
    "PrincipalKind",
    "StorageScope",
]
