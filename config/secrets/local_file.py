"""Owner-only local file store for secrets (``~/.opensre/credentials.json``).

This is the same shape every CLI that must work on a headless box settles on —
``~/.aws/credentials``, ``~/.config/gh/hosts.yml``, ``~/.docker/config.json`` —
a ``0600`` file in the user's home. It is deliberately *not* the project
``.env``: that file is more likely to be committed, copied into an image, or
shared, so this store remains the owner-only fallback.

Encrypting it was considered and rejected: a passphrase prompt defeats the
headless case this exists for, and a key stored beside the ciphertext protects
nothing. File permissions are the actual control, so they are established at
creation rather than narrowed afterwards.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from filelock import FileLock
from filelock import Timeout as FileLockTimeout

from config.constants.paths import host_home
from config.constants.secrets import CREDENTIAL_FALLBACK_FILENAME

_VERSION = 1
_LOCK_TIMEOUT_SECONDS = 10.0


class LocalStoreError(Exception):
    """The credential file could not be read or written.

    Raised for ordinary I/O failures and for lock contention. ``FileLock``
    raises :class:`filelock.Timeout` on the finite wait; that type is converted
    here so migration and lookup never need to special-case ``filelock``.
    """


# Callers that must not abort on a contended store catch this tuple. It names
# both our wrapper and ``OSError`` so file permission failures stay covered.
LOCAL_STORE_ERRORS: tuple[type[BaseException], ...] = (LocalStoreError, OSError)


def store_path() -> Path:
    """Where the fallback store lives.

    Anchored to :func:`host_home` rather than the org-scoped
    :func:`config.constants.paths.opensre_home`: a deployed silo gets its
    credentials from the environment, and pointing this at a customer-owned
    volume would put one organization's secrets on another's mount.
    """
    return host_home() / CREDENTIAL_FALLBACK_FILENAME


def _lock_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with suppress(OSError):
        path.parent.chmod(0o700)


@contextmanager
def _store_lock(path: Path) -> Iterator[None]:
    """Hold the credential-file lock, translating lock timeouts to LocalStoreError."""
    try:
        with FileLock(str(_lock_path(path)), timeout=_LOCK_TIMEOUT_SECONDS):
            yield
    except FileLockTimeout as exc:
        raise LocalStoreError(
            f"Timed out after {_LOCK_TIMEOUT_SECONDS:g}s waiting for the credential store lock."
        ) from exc


#: What identifies one published version of the store: every write replaces the
#: file (new inode, size and timestamps), so an unchanged signature means the
#: contents read under the lock are still current.
_FileSignature = tuple[int, int, int, int, int]
#: A file modified this recently is not cached: on filesystems with coarse
#: timestamps a second write inside the same tick could keep the signature.
_RACY_WINDOW_NS = 2_000_000_000


@dataclass(frozen=True)
class _StoreSnapshot:
    path: Path
    signature: _FileSignature
    secrets: Mapping[str, str]


class _ReadCache:
    """The last store contents read under the lock in this process.

    Startup resolves the same credentials hundreds of times (every tool
    availability check); re-reading only when the file changed keeps each
    lookup a ``stat`` instead of a lock, a read and a JSON parse.
    """

    snapshot: _StoreSnapshot | None = None


def clear_read_cache() -> None:
    """Forget the cached store contents (tests; the next read takes the lock)."""
    _ReadCache.snapshot = None


def _signature(path: Path) -> _FileSignature | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _read_secrets(path: Path) -> Mapping[str, str]:
    """The stored secrets, reused while the file is unchanged since the last locked read."""
    signature = _signature(path)
    if signature is None:
        return MappingProxyType({})
    cached = _ReadCache.snapshot
    if cached is not None and cached.path == path and cached.signature == signature:
        return cached.secrets
    with _store_lock(path):
        # Taken under the lock: writers publish with ``os.replace`` while
        # holding it, so this signature names exactly the contents read next.
        signature = _signature(path)
        if signature is None:
            return MappingProxyType({})
        secrets: Mapping[str, str] = MappingProxyType(_load_unlocked(path))
    changed_ns = max(signature[3], signature[4])
    if time.time_ns() - changed_ns >= _RACY_WINDOW_NS:
        _ReadCache.snapshot = _StoreSnapshot(path, signature, secrets)
    return secrets


def _load_unlocked(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    secrets = data.get("secrets")
    if not isinstance(secrets, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in secrets.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _write_unlocked(path: Path, secrets: Mapping[str, str]) -> None:
    """Publish the store atomically, never existing world-readable on the way.

    ``mkstemp`` creates at ``0600``, the content is written into that already
    restricted file, and ``os.replace`` swaps it in. Writing the plaintext first
    and narrowing permissions afterwards would leave a window where any local
    user can read the file.
    """
    _ensure_parent(path)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": _VERSION, "secrets": dict(secrets)}, handle, sort_keys=True)
            handle.write("\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def get(env_var: str) -> str:
    """Return the stored secret, or ``""`` when absent.

    Raises :class:`LocalStoreError` when the store lock times out.
    """
    try:
        return _read_secrets(store_path()).get(env_var, "").strip()
    except OSError as exc:
        raise LocalStoreError("Reading the credential store failed.") from exc


def keys() -> tuple[str, ...]:
    """Names currently stored in the local file (no values).

    Raises :class:`LocalStoreError` when the store lock times out.
    """
    try:
        return tuple(_read_secrets(store_path()))
    except OSError as exc:
        raise LocalStoreError("Listing credential store keys failed.") from exc


def set(env_var: str, value: str) -> None:  # noqa: A001 - mirrors get/delete in this tier
    """Store a secret, replacing any existing entry.

    Raises :class:`LocalStoreError` when the store lock times out or the write fails.
    """
    path = store_path()
    try:
        _ensure_parent(path)
        with _store_lock(path):
            secrets = _load_unlocked(path)
            secrets[env_var] = value
            _write_unlocked(path, secrets)
            clear_read_cache()
    except OSError as exc:
        raise LocalStoreError("Writing the credential store failed.") from exc


def delete(env_var: str) -> None:
    """Remove a secret, tolerating an absent entry or store.

    Raises :class:`LocalStoreError` when the store lock times out.
    """
    path = store_path()
    if not path.exists():
        return
    try:
        with _store_lock(path):
            secrets = _load_unlocked(path)
            if env_var not in secrets:
                return
            del secrets[env_var]
            _write_unlocked(path, secrets)
            clear_read_cache()
    except OSError as exc:
        raise LocalStoreError("Updating the credential store failed.") from exc


__all__ = [
    "LOCAL_STORE_ERRORS",
    "LocalStoreError",
    "clear_read_cache",
    "delete",
    "get",
    "keys",
    "set",
    "store_path",
]
