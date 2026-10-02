"""The on-disk cache of verified skills releases, shared by every local process.

Layout under ``host_home()/skills`` (never the org mount, which can hang):

- ``releases/release-<seq>.json`` — verified release documents, written atomically.
- ``state.json`` — the background puller's ETag and last check time.
- ``announced.json`` — the last release reported to analytics from this machine.
- ``run/<process>/<seq>-<nonce>/`` — one process's materialized catalogs (helper
  scripts run from them). The process holds ``run/<process>/owner.lock`` for its
  lifetime, so another process can tell a dead owner's directory by taking it.

Release files are immutable and named by sequence, so concurrent writers can
only ever race to write identical bytes.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from config.constants.paths import host_home
from config.constants.skills import SKILLS_RELEASES_KEPT
from core.agent_harness.prompts.skills.snapshot.release import SkillsRelease

logger = logging.getLogger(__name__)

_RELEASE_PREFIX = "release-"
_RELEASE_SUFFIX = ".json"
_OWNER_LOCK = "owner.lock"
_process_root: tuple[Path, FileLock] | None = None
_process_root_lock = threading.Lock()


def store_dir() -> Path:
    """Return the store root, read at call time so tests can redirect the host home."""
    return host_home() / "skills"


def releases_dir() -> Path:
    """Return the directory holding verified release documents."""
    return store_dir() / "releases"


def store_stamp() -> int | None:
    """Return a cheap change marker for the releases directory (``None`` when absent)."""
    try:
        return releases_dir().stat().st_mtime_ns
    except OSError:
        return None


def _release_seq(path: Path) -> int | None:
    name = path.name
    if not (name.startswith(_RELEASE_PREFIX) and name.endswith(_RELEASE_SUFFIX)):
        return None
    digits = name[len(_RELEASE_PREFIX) : -len(_RELEASE_SUFFIX)]
    return int(digits) if digits.isdigit() else None


def _release_paths() -> list[tuple[int, Path]]:
    try:
        entries = list(releases_dir().iterdir())
    except OSError:
        return []
    numbered = [(seq, path) for path in entries if (seq := _release_seq(path)) is not None]
    return sorted(numbered, reverse=True)


def stored_releases() -> list[SkillsRelease]:
    """Return readable stored releases, newest first; unreadable files are skipped."""
    releases: list[SkillsRelease] = []
    for seq, path in _release_paths():
        try:
            release = SkillsRelease.from_document(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            logger.debug("Ignoring unreadable skills release %s: %s", path, exc)
            continue
        if release.seq == seq:
            releases.append(release)
    return releases


def latest_stored_seq() -> int | None:
    """Return the newest stored release sequence without parsing documents."""
    paths = _release_paths()
    return paths[0][0] if paths else None


def _write_atomically(target: Path, payload: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(temp_name, target)
    finally:
        # Already gone after a successful replace; removes the partial file otherwise.
        with suppress(OSError):
            os.unlink(temp_name)


def write_release(release: SkillsRelease) -> Path:
    """Store a verified release and prune all but the newest few."""
    target = releases_dir() / f"{_RELEASE_PREFIX}{release.seq}{_RELEASE_SUFFIX}"
    if not target.exists():
        _write_atomically(target, json.dumps(release.to_document(), ensure_ascii=False))
    for _seq, stale in _release_paths()[SKILLS_RELEASES_KEPT:]:
        with suppress(OSError):
            stale.unlink()
    return target


def read_state() -> dict[str, Any]:
    """Return the puller's persisted state (empty when missing or unreadable)."""
    try:
        state = json.loads((store_dir() / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def write_state(state: dict[str, Any]) -> None:
    """Persist the puller's state atomically."""
    _write_atomically(store_dir() / "state.json", json.dumps(state))


def read_announced() -> str:
    """Return the last release reported to analytics from this machine."""
    try:
        value = json.loads((store_dir() / "announced.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return value.get("release", "") if isinstance(value, dict) else ""


def write_announced(release: str) -> None:
    """Record ``release`` as reported (its own file, so it never races the puller's state)."""
    _write_atomically(store_dir() / "announced.json", json.dumps({"release": release}))


def _owned_process_root() -> Path:
    """This process's run directory, created once and held under its owner lock."""
    global _process_root
    with _process_root_lock:
        if _process_root is None or not _process_root[0].is_dir():
            root = store_dir() / "run" / f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
            root.mkdir(parents=True, exist_ok=True)
            lock = FileLock(str(root / _OWNER_LOCK))
            lock.acquire()
            _process_root = (root, lock)
        return _process_root[0]


def new_run_root(seq: int) -> Path:
    """Return a fresh, not-yet-created directory for this process to materialize ``seq``.

    The caller removes it when the snapshot built from it is gone (no turn can
    still run its scripts); :func:`sweep_run_roots` removes dead processes' roots.
    """
    return _owned_process_root() / f"{seq}-{uuid.uuid4().hex[:8]}"


def sweep_run_roots() -> None:
    """Delete run directories whose owning process has exited."""
    own = _process_root[0] if _process_root is not None else None
    try:
        process_dirs = list((store_dir() / "run").iterdir())
    except OSError:
        return
    for process_dir in process_dirs:
        if process_dir == own or not process_dir.is_dir():
            continue
        probe = FileLock(str(process_dir / _OWNER_LOCK), timeout=0)
        try:
            probe.acquire()
        except Timeout:
            continue  # its owner is alive
        except OSError:
            continue
        try:
            shutil.rmtree(process_dir, ignore_errors=True)
        finally:
            with suppress(OSError):
                probe.release()


__all__ = [
    "latest_stored_seq",
    "new_run_root",
    "read_announced",
    "read_state",
    "releases_dir",
    "store_dir",
    "store_stamp",
    "stored_releases",
    "sweep_run_roots",
    "write_announced",
    "write_release",
    "write_state",
]
