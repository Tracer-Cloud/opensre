"""Register object-store backends without changing the sync engine.

The engine talks only to :class:`~infrastructure.filestorage.contracts.ObjectStore`.
Each cloud vendor ships a factory here; surfaces call
:func:`build_object_store` and never import a vendor module directly.
Adding GCS, Azure, or another backend is a new module plus one
``register`` call — the engine, CLI, and REPL stay closed (open/closed
principle).

The registry dict is process-global (plugin table). Mutations and lookups are
serialized with an ``RLock`` so concurrent ``build_object_store`` calls from
the shared sync service stay safe. Factories run **outside** the lock.
"""

from __future__ import annotations

import importlib
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from config.constants.filestorage import DEFAULT_MAX_PARALLEL_UPLOADS
from infrastructure.filestorage.enums import BucketExposure, BuiltInProvider, RemoteSyncField
from infrastructure.filestorage.errors import RemoteSyncConfigError
from infrastructure.filestorage.exposure import PublicAccessChecker, PublicAccessStatus, not_checked

if TYPE_CHECKING:
    from infrastructure.filestorage.config import RemoteSyncConfig
    from infrastructure.filestorage.contracts import ObjectStore

ObjectStoreFactory = Callable[["RemoteSyncConfig"], "ObjectStore"]

_DEFAULT_CREDENTIAL_HINT = "Use ambient credentials for this provider; opensre does not store them."


@dataclass(frozen=True)
class SetupExtraField:
    """One provider-specific setup field, declared by the provider itself.

    Mirrors ``integrations.setup_flow.SetupField`` in spirit — a provider
    declares what it needs, so ``setup.py`` and the CLI act on the
    declaration instead of branching on the provider's name. Kept minimal on
    purpose: unlike an integration's arbitrary credentials dict,
    ``RemoteSyncConfig`` has exactly two optional slots (``region``,
    ``profile``) and nothing here is ever a secret, so there is no
    env_var/secret/constant machinery to carry.
    """

    field: RemoteSyncField
    """Which of ``RemoteSyncConfig``'s two extension slots this fills."""

    prompt: str
    """Question text when collecting this field interactively."""


_REGISTRY: dict[str, ObjectStoreFactory] = {}
_CREDENTIAL_HINTS: dict[str, str] = {}
_EXTRA_FIELDS: dict[str, tuple[SetupExtraField, ...]] = {}
_PUBLIC_ACCESS_CHECKERS: dict[str, PublicAccessChecker] = {}
_MAX_PARALLEL_UPLOADS: dict[str, int] = {}
_REGISTRY_LOCK = threading.RLock()

# Built-in backends, imported on first use so this package never imports itself.
# Each module calls ``register_object_store`` at import time; a third party can
# register ahead of time instead and never appear here.
_BUILTIN_MODULES = {
    BuiltInProvider.AWS.value: "infrastructure.filestorage.providers.aws",
    BuiltInProvider.GCS.value: "infrastructure.filestorage.providers.gcs",
    BuiltInProvider.VERCEL.value: "infrastructure.filestorage.providers.vercel",
    BuiltInProvider.AZURE.value: "infrastructure.filestorage.providers.azure",
    BuiltInProvider.S3COMPAT.value: "infrastructure.filestorage.providers.s3compat",
}


def _load_builtin(key: str) -> None:
    """Import a built-in module under the registry lock (caller holds it).

    ``importlib.import_module`` is a no-op when the module is already in
    ``sys.modules``. Tests (and only tests) may clear the registry while
    leaving those modules imported; reload so the builtin registers again.
    """
    module_name = _BUILTIN_MODULES.get(key)
    if module_name is None or key in _REGISTRY:
        return
    existing = sys.modules.get(module_name)
    if existing is not None:
        importlib.reload(existing)
        return
    importlib.import_module(module_name)


def register_object_store(
    name: str,
    factory: ObjectStoreFactory,
    *,
    credential_hint: str | None = None,
    extra_fields: tuple[SetupExtraField, ...] = (),
    public_access_checker: PublicAccessChecker | None = None,
    max_parallel_uploads: int | None = None,
) -> None:
    """Bind ``name`` (the value of ``OPENSRE_REMOTE_SYNC_PROVIDER``) to a factory.

    ``credential_hint`` is the one-line, secret-free sentence shown after
    ``setup`` about where this provider's ambient credentials come from (see
    :func:`credential_hint_for_provider`). Omit it to fall back to a generic
    sentence.

    ``extra_fields`` declares which of ``RemoteSyncConfig``'s ``region``/
    ``profile`` slots this provider consumes (see :func:`provider_extra_fields`).
    Omit it — as most providers do — when the provider needs neither.

    ``public_access_checker`` answers whether the store is publicly readable
    (see :func:`check_bucket_exposure`). Omit it when the provider has no way
    to ask — ``status`` then reports the exposure as unchecked rather than
    guessing.

    ``max_parallel_uploads`` is how many ``put_object`` calls a push may have
    in flight against this backend (see :func:`max_parallel_uploads_for_provider`).
    Declared by the provider rather than fixed in the engine because the
    backends are not interchangeable here: one behind an SDK that retries
    throttling absorbs a burst that would make another — a bare HTTP client
    with no retry, where a single 429 aborts the whole push — fail. Omit it to
    inherit the deliberately low default.
    """
    key = name.strip().lower()
    if not key:
        raise ValueError("object-store provider name must be non-empty")
    if max_parallel_uploads is not None and max_parallel_uploads < 1:
        raise ValueError("max_parallel_uploads must be at least 1")
    with _REGISTRY_LOCK:
        _REGISTRY[key] = factory
        if credential_hint is not None:
            _CREDENTIAL_HINTS[key] = credential_hint
        _EXTRA_FIELDS[key] = extra_fields
        if public_access_checker is None:
            _PUBLIC_ACCESS_CHECKERS.pop(key, None)
        else:
            _PUBLIC_ACCESS_CHECKERS[key] = public_access_checker
        if max_parallel_uploads is None:
            _MAX_PARALLEL_UPLOADS.pop(key, None)
        else:
            _MAX_PARALLEL_UPLOADS[key] = max_parallel_uploads


def unregister_object_store(name: str) -> None:
    """Drop a registration (tests / experimental backends)."""
    with _REGISTRY_LOCK:
        key = name.strip().lower()
        _REGISTRY.pop(key, None)
        _CREDENTIAL_HINTS.pop(key, None)
        _EXTRA_FIELDS.pop(key, None)
        _PUBLIC_ACCESS_CHECKERS.pop(key, None)
        _MAX_PARALLEL_UPLOADS.pop(key, None)


def registered_providers() -> tuple[str, ...]:
    """Sorted provider names available, whether or not they are loaded yet."""
    with _REGISTRY_LOCK:
        names = set(_REGISTRY) | set(_BUILTIN_MODULES)
    return tuple(sorted(names))


def builtin_providers() -> tuple[str, ...]:
    """Sorted names of the shipped built-in backends (community registrations excluded)."""
    return tuple(sorted(_BUILTIN_MODULES))


def build_object_store(config: RemoteSyncConfig) -> ObjectStore:
    """Construct the store for ``config.provider``.

    Unknown names fail closed with the list of known providers so the operator
    can fix the env var without reading source. The factory runs outside the
    registry lock so a slow client build does not block other providers.
    """
    key = config.provider.strip().lower()
    with _REGISTRY_LOCK:
        _load_builtin(key)
        factory = _REGISTRY.get(key)
        known = tuple(sorted(set(_REGISTRY) | set(_BUILTIN_MODULES)))
    if factory is None:
        listed = ", ".join(known) or "(none)"
        raise RemoteSyncConfigError(
            f"unknown remote-sync provider {config.provider!r}; known: {listed}"
        )
    return factory(config)


def credential_hint_for_provider(provider: str) -> str:
    """One line on ambient credentials for ``provider`` (no secrets).

    Loads a not-yet-imported built-in module first, so this works even before
    any store has been built for ``provider``.
    """
    key = provider.strip().lower()
    with _REGISTRY_LOCK:
        _load_builtin(key)
        hint = _CREDENTIAL_HINTS.get(key)
    return hint if hint is not None else _DEFAULT_CREDENTIAL_HINT


def check_bucket_exposure(config: RemoteSyncConfig) -> PublicAccessStatus:
    """Ask ``config.provider`` whether ``config.bucket`` is publicly readable.

    Loads a not-yet-imported built-in module first, same as
    :func:`credential_hint_for_provider`. A provider with no registered
    checker resolves to :func:`~infrastructure.filestorage.exposure.not_checked`.

    Never raises: a checker is community-authored code as far as this
    registry is concerned, and an exposure check is a best-effort courtesy on
    top of ``status``/``setup``, not something either command should fail
    for. An unexpected exception degrades to ``UNKNOWN`` with only the
    exception's type named — not its message, which could carry provider
    internals — matching the same failure the checker itself is asked to
    report for a merely unreachable store.
    """
    key = config.provider.strip().lower()
    with _REGISTRY_LOCK:
        _load_builtin(key)
        checker = _PUBLIC_ACCESS_CHECKERS.get(key)
    if checker is None:
        return not_checked(config.provider)
    try:
        return checker(config)
    except Exception as exc:  # noqa: BLE001 - see docstring: a checker must never fail status/setup
        return PublicAccessStatus(
            BucketExposure.UNKNOWN, f"exposure check raised {type(exc).__name__}"
        )


def max_parallel_uploads_for_provider(provider: str) -> int:
    """How many concurrent ``put_object`` calls ``provider`` accepts.

    Loads a not-yet-imported built-in module first, same as
    :func:`credential_hint_for_provider`. A provider that declared nothing —
    and an unknown name — resolves to
    :data:`~config.constants.filestorage.DEFAULT_MAX_PARALLEL_UPLOADS`, so a
    backend nobody has profiled is never pushed harder than the cautious
    default.
    """
    key = provider.strip().lower()
    with _REGISTRY_LOCK:
        _load_builtin(key)
        return _MAX_PARALLEL_UPLOADS.get(key, DEFAULT_MAX_PARALLEL_UPLOADS)


def provider_extra_fields(provider: str) -> tuple[SetupExtraField, ...]:
    """``region``/``profile`` fields ``provider`` declared, or ``()`` for neither.

    Loads a not-yet-imported built-in module first, same as
    :func:`credential_hint_for_provider`, so this is accurate even before any
    store has been built for ``provider``. An unknown provider name also
    resolves to ``()`` — callers that need to reject unknown providers do so
    separately (:func:`registered_providers`).
    """
    key = provider.strip().lower()
    with _REGISTRY_LOCK:
        _load_builtin(key)
        return _EXTRA_FIELDS.get(key, ())


__all__ = [
    "ObjectStoreFactory",
    "SetupExtraField",
    "build_object_store",
    "builtin_providers",
    "check_bucket_exposure",
    "credential_hint_for_provider",
    "max_parallel_uploads_for_provider",
    "provider_extra_fields",
    "register_object_store",
    "registered_providers",
    "unregister_object_store",
]
