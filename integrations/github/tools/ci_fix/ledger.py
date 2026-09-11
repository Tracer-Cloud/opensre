"""Process-local CI repair counts backed by an organization-owned ledger."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import RLock

from filelock import Timeout

from config.constants import ci_fix_ledger_path
from integrations.github.tools.ci_fix.ledger_store import append_fix_ids, read_fix_ids

logger = logging.getLogger(__name__)


class CiFixCounter:
    """Deduplicate repairs in memory and notify local listeners without polling."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = RLock()
        self._identities: set[str] = set()
        self._known = False
        self._listeners: dict[object, Callable[[], None]] = {}
        self.reload()

    def count(self) -> int:
        """Return the cached count, or zero until a baseline can be read or recovered."""
        with self._lock:
            return len(self._identities) if self._known else 0

    def reload(self) -> int:
        """Merge disk with locally observed repairs, including failed saves."""
        try:
            stored = read_fix_ids(self._path)
        except (OSError, ValueError):
            return self.count()
        with self._lock:
            before = self.count()
            self._identities.update(stored)
            self._known = True
            changed = self.count() != before
        if changed:
            self._notify()
        return self.count()

    def record(self, identity: str) -> None:
        """Publish the local repair before attempting its best-effort disk write."""
        with self._lock:
            before = self.count()
            self._identities.add(identity)
            identities = self._identities.copy()
            changed = self.count() != before
        if changed:
            self._notify()
        try:
            stored = append_fix_ids(self._path, identities)
        except (OSError, ValueError, Timeout):
            logger.warning("Could not persist the CI fix counter", exc_info=True)
            return
        with self._lock:
            before = self.count()
            self._identities.update(stored)
            self._known = True
            changed = self.count() != before
        if changed:
            self._notify()

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a redraw notification and return its idempotent cleanup."""
        token = object()
        with self._lock:
            self._listeners[token] = callback

        def unsubscribe() -> None:
            with self._lock:
                self._listeners.pop(token, None)

        return unsubscribe

    def _notify(self) -> None:
        with self._lock:
            callbacks = tuple(self._listeners.values())
        for callback in callbacks:
            try:
                callback()
            except Exception:
                logger.debug("CI fix count listener failed", exc_info=True)


_counters: dict[Path, CiFixCounter] = {}
_counters_lock = RLock()


def get_ci_fix_counter() -> CiFixCounter:
    """Resolve the counter for the current deployment's ledger."""
    path = ci_fix_ledger_path().resolve()
    with _counters_lock:
        counter = _counters.get(path)
        if counter is None:
            counter = CiFixCounter(path)
            _counters[path] = counter
        return counter


def count_ci_fixes() -> int:
    """Reload the local banner count without network calls or vendor clients."""
    try:
        return get_ci_fix_counter().reload()
    except Exception:
        logger.debug("CI fix counter unavailable", exc_info=True)
        return 0


def record_ci_fix_outcome(output: Mapping[str, object]) -> None:
    """Record a verified repair without affecting the tool's successful outcome."""
    try:
        identity = _repair_identity(output)
        if identity is not None:
            get_ci_fix_counter().record(identity)
    except Exception:
        logger.warning("Could not record the completed CI fix", exc_info=True)


def _repair_identity(output: Mapping[str, object]) -> str | None:
    # Verification imports the repair execution graph; banner reads must stay cheap.
    from integrations.github.tools.ci_fix.verification import CheckState

    if output.get("success") is not True or output.get("checks_state") != CheckState.PASSED.value:
        return None
    owner, repo, sha = (output.get(key) for key in ("owner", "repo", "source_head_sha"))
    if not all(isinstance(value, str) and value.strip() for value in (owner, repo, sha)):
        return None
    kind = output.get("target_type")
    target = output.get("pr_number") if kind == "pr" else output.get("target_branch")
    if kind == "pr":
        if type(target) is not int or target <= 0:
            return None
    elif kind != "branch" or not isinstance(target, str) or not target:
        return None
    parts = (str(owner).strip().casefold(), str(repo).strip().casefold(), kind, target, str(sha))
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


__all__ = ["CiFixCounter", "count_ci_fixes", "get_ci_fix_counter", "record_ci_fix_outcome"]
