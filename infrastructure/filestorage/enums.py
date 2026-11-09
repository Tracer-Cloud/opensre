"""Closed vocabularies for remote sync.

Open/extensible names (community object-store providers) stay plain strings in
the registry. Use these enums only where the set is fixed and shared across
engine + surfaces.
"""

from __future__ import annotations

from enum import StrEnum


class SyncDirection(StrEnum):
    """Which way one sync moves files."""

    BOTH = "both"
    PULL = "pull"
    PUSH = "push"


class SyncRootName(StrEnum):
    """Allowlisted roots that may leave the machine (object-key prefixes)."""

    SESSIONS = "sessions"
    MEMORY = "memory"


class BuiltInProvider(StrEnum):
    """Shipped object-store backends.

    Community providers register under any other string name — they are not
    listed here on purpose, so this enum stays closed.
    """

    AWS = "aws"
    GCS = "gcs"
    VERCEL = "vercel"
    AZURE = "azure"
    S3COMPAT = "s3compat"
    NEON = "neon"


class RemoteSyncSubcommand(StrEnum):
    """CLI / slash verbs shared by ``opensre remote-sync`` and ``/remote-sync``."""

    STATUS = "status"
    SYNC = "sync"
    SETUP = "setup"


class RemoteSyncField(StrEnum):
    """Provider-specific setup fields ``RemoteSyncConfig`` can hold.

    Fixed at two on purpose — ``RemoteSyncConfig`` is a closed, two-slot
    contract for the fields a provider may need beyond bucket/prefix. A
    provider declares which of these it consumes (see
    :class:`infrastructure.filestorage.providers.registry.SetupExtraField`); it does
    not gain new attributes of its own.
    """

    REGION = "region"
    PROFILE = "profile"


class BucketExposure(StrEnum):
    """Whether a store is readable by anyone on the internet.

    ``UNKNOWN`` covers every reason the question could not be answered — no
    checker registered for the provider, a missing permission, or an
    unreachable store — so a caller never has to distinguish those from a
    definitive answer (see :mod:`infrastructure.filestorage.exposure`).
    """

    PRIVATE = "private"
    PUBLIC = "public"
    UNKNOWN = "unknown"


__all__ = [
    "BucketExposure",
    "BuiltInProvider",
    "RemoteSyncField",
    "RemoteSyncSubcommand",
    "SyncDirection",
    "SyncRootName",
]
