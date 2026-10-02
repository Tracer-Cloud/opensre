"""The on-disk cache of verified skills releases, shared by every local process.

Layout under ``host_home()/skills`` (never the org mount, which can hang):

- ``releases/release-<seq>.json`` — verified release documents, written atomically.
- ``state.json`` — the background puller's ETag and last check time.
- ``run/<pid>/<seq>/`` — one process's materialized catalog (scripts run from it).

Release files are immutable and named by sequence, so concurrent writers can
only ever race to write identical bytes.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

import psutil

from config.constants.paths import host_home
from config.constants.skills import SKILLS_RELEASES_KEPT
from core.agent_harness.prompts.skills.snapshot.release import SkillsRelease

logger = logging.getLogger(__name__)

_RELEASE_PREFIX = "release-"
_RELEASE_SUFFIX = ".json"
_RUN_ROOTS_KEPT = 2


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


def new_run_root(seq: int) -> Path:
    """Return a fresh, not-yet-created directory for this process to materialize ``seq``."""
    return store_dir() / "run" / str(os.getpid()) / f"{seq}-{uuid.uuid4().hex[:8]}"


def sweep_run_roots(keep: Path | None = None) -> None:
    """Delete run roots of dead processes and this process's older materializations."""
    run_dir = store_dir() / "run"
    try:
        process_dirs = list(run_dir.iterdir())
    except OSError:
        return
    own = str(os.getpid())
    for process_dir in process_dirs:
        if process_dir.name == own:
            _prune_own_roots(process_dir, keep)
        elif not process_dir.name.isdigit() or not psutil.pid_exists(int(process_dir.name)):
            shutil.rmtree(process_dir, ignore_errors=True)


def _prune_own_roots(process_dir: Path, keep: Path | None) -> None:
    try:
        roots = sorted(process_dir.iterdir(), key=lambda path: path.stat().st_mtime_ns)
    except OSError:
        return
    stale = [root for root in roots if root != keep][: max(0, len(roots) - _RUN_ROOTS_KEPT)]
    for root in stale:
        shutil.rmtree(root, ignore_errors=True)


__all__ = [
    "latest_stored_seq",
    "new_run_root",
    "read_state",
    "releases_dir",
    "store_dir",
    "store_stamp",
    "stored_releases",
    "sweep_run_roots",
    "write_release",
    "write_state",
]
