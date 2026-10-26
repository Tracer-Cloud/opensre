"""Whether the configured store is publicly readable — an optional provider capability.

Kept apart from :mod:`infrastructure.filestorage.contracts` on purpose: the engine's
``ObjectStore`` protocol stays at its four operations for every backend.
Answering "can anyone on the internet read this?" needs vendor-specific APIs
(S3's ``GetBucketPolicyStatus`` today) that most providers, and every
community backend, will never implement — so a provider opts in by
registering a checker (see
:func:`infrastructure.filestorage.providers.registry.register_object_store`)
instead of the protocol growing a fifth method every backend must satisfy.

A checker is never load-bearing: it degrades to
:class:`~infrastructure.filestorage.enums.BucketExposure.UNKNOWN` rather than
raising, so a stalled or denied exposure check never blocks ``status`` or
``setup`` — only ``PUBLIC`` is worth interrupting anyone's day for.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.enums import BucketExposure


@dataclass(frozen=True)
class PublicAccessStatus:
    """Best-effort answer to "is this store publicly readable?"

    ``detail`` is empty for a definitive ``PRIVATE``/``PUBLIC`` result and
    carries a short, operator-facing reason whenever ``exposure`` is
    ``UNKNOWN``.
    """

    exposure: BucketExposure
    detail: str = ""


# A plain (non-``TYPE_CHECKING``, unquoted) import: ``RemoteSyncConfig`` is only
# ever referenced here, inside a runtime expression, not an annotation position
# — a quoted forward reference under ``TYPE_CHECKING`` type-checks fine but
# reads as an unused import to a linter that doesn't parse type strings inside
# a bare ``Callable[[...]]`` subscript. ``config.py`` doesn't import this
# module, so there is no cycle to guard against.
PublicAccessChecker = Callable[[RemoteSyncConfig], PublicAccessStatus]


def not_checked(provider: str) -> PublicAccessStatus:
    """Result for a provider that has not registered an exposure checker."""
    return PublicAccessStatus(
        BucketExposure.UNKNOWN, f"public-access check not implemented for provider {provider!r}"
    )


__all__ = ["PublicAccessChecker", "PublicAccessStatus", "not_checked"]
