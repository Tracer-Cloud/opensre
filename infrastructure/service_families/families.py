"""Service-family key normalization for the tool-availability layer.

Tool resolution groups tools by "family" (multi-instance service
buckets, e.g. all Datadog log ingest variants collapse into the ``datadog``
family). Historically the mapping lived only in
:mod:`integrations.registry`, and tool-resolution code under ``tools/``
imported it directly — an illegal ``tools -> integrations`` edge (T-4 layering
audit, issue #3352, item 27).

This module inverts the dependency: it declares a tiny normalizer callable and
a default identity implementation. The integration layer registers the real
mapping at import time via :func:`register_family_key_resolver`. The tools
layer calls :func:`family_key` without ever importing from ``integrations``.
"""

from __future__ import annotations

from collections.abc import Callable

FamilyKeyResolver = Callable[[str], str]

_resolver: FamilyKeyResolver | None = None


def register_family_key_resolver(resolver: FamilyKeyResolver | None) -> None:
    """Bind (or clear) the concrete family-key resolver.

    Called from :mod:`integrations.registry` at import time. Passing ``None``
    clears the binding — useful in tests that want to exercise the identity
    fallback.
    """
    global _resolver
    _resolver = resolver


def family_key(service_key: str) -> str:
    """Return the multi-instance family key for ``service_key``.

    Falls back to the identity function when no resolver has been registered.
    That fallback is deliberately conservative: it keeps callers deterministic
    when the integration layer hasn't yet been imported (e.g. lightweight
    unit tests) at the cost of not collapsing siblings into their canonical
    bucket.
    """
    if _resolver is None:
        return service_key
    return _resolver(service_key)


__all__ = ["FamilyKeyResolver", "family_key", "register_family_key_resolver"]
