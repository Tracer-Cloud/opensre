"""Choose which catalog a process runs: local override, newest valid release, or bundle.

Precedence: ``OPENSRE_SKILLS_DIR`` (exclusive, for authoring) > the newest
stored release that verifies and fits this binary > the cards bundled in the
binary. A release is accepted or rejected whole, never mixed with bundled cards.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
import weakref
from dataclasses import replace
from pathlib import Path

from config.constants.skills import SKILLS_API_VERSION, SKILLS_DIR_ENV
from config.skills_auto_update import skills_auto_update_enabled
from config.version import get_opensre_version
from core.agent_harness.prompts.skills.catalog.reader import read_skill_catalog
from core.agent_harness.prompts.skills.content import files
from core.agent_harness.prompts.skills.snapshot.build import build_snapshot
from core.agent_harness.prompts.skills.snapshot.catalog_snapshot import (
    SkillCatalogSnapshot,
    SkillSource,
)
from core.agent_harness.prompts.skills.snapshot.release import (
    ReleaseError,
    SkillsRelease,
    materialize_release,
    trusted_release_keys,
    verify_release,
)
from core.agent_harness.prompts.skills.snapshot.release_store import (
    new_run_root,
    releases_dir,
    store_stamp,
    stored_releases,
    sweep_run_roots,
)

logger = logging.getLogger(__name__)

_TREE_SUFFIXES = (".md", ".py")

#: Release sequences this process already rejected, with the reason.
_rejected: dict[int, str] = {}
_rejected_lock = threading.Lock()


def auto_update_enabled() -> bool:
    """True when stored releases may be activated (default: release binaries only)."""
    return skills_auto_update_enabled()


def override_root() -> Path | None:
    """Return the ``OPENSRE_SKILLS_DIR`` catalog, or ``None`` when unset or missing."""
    raw = os.getenv(SKILLS_DIR_ENV, "").strip()
    if not raw:
        return None
    root = Path(raw).expanduser()
    if root.is_dir():
        return root
    logger.warning("%s=%s is not a directory; using the default skills", SKILLS_DIR_ENV, raw)
    return None


def _tree_stamp(root: Path) -> tuple[int, int]:
    """Return ``(file count, newest mtime)`` over the catalog's Markdown and scripts."""
    count = 0
    newest = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            if filename.endswith(_TREE_SUFFIXES):
                count += 1
                try:
                    newest = max(newest, os.stat(os.path.join(dirpath, filename)).st_mtime_ns)
                except OSError:
                    continue
    return count, newest


def source_stamp() -> tuple[object, ...]:
    """Return a cheap marker that changes whenever :func:`load_snapshot` would differ."""
    override = override_root()
    if override is not None:
        return ("override", str(override), _tree_stamp(override))
    if auto_update_enabled():
        return ("store", str(releases_dir()), store_stamp())
    return ("bundled", str(files.skills_dir()))


def _catalog_release(snapshot: SkillCatalogSnapshot, prefix: str) -> str:
    joined = "".join(f"{name}:{snapshot.digests[name]}\n" for name in sorted(snapshot.digests))
    return f"{prefix}:{hashlib.sha256(joined.encode('utf-8')).hexdigest()[:12]}"


def _bundled_names() -> frozenset[str]:
    return frozenset(skill.name for skill in read_skill_catalog(files.skills_dir()).skills)


def _reject(seq: int, reason: str) -> None:
    with _rejected_lock:
        first = seq not in _rejected
        _rejected[seq] = reason
    if first:
        logger.warning("Not activating skills release %s: %s", seq, reason)


def _release_snapshot(
    release: SkillsRelease, required: frozenset[str]
) -> SkillCatalogSnapshot | None:
    try:
        if release.skill_api > SKILLS_API_VERSION:
            raise ReleaseError(
                f"needs skills API {release.skill_api}; this binary supports {SKILLS_API_VERSION}"
            )
        verify_release(release, trusted_release_keys())
        root = materialize_release(release, new_run_root(release.seq))
    except (ReleaseError, OSError, UnicodeError) as exc:
        _reject(release.seq, str(exc))
        return None
    snapshot = build_snapshot(
        root,
        source=SkillSource.REMOTE,
        release=f"remote:{release.seq}",
        release_seq=release.seq,
    )
    missing = required - {skill.name for skill in snapshot.skills}
    if snapshot.diagnostics or missing:
        problems = list(snapshot.diagnostics[:3])
        if missing:
            problems.append(f"missing skills this binary relies on: {sorted(missing)}")
        _reject(release.seq, "; ".join(problems))
        shutil.rmtree(root, ignore_errors=True)
        return None
    # The directory lives exactly as long as a turn or the catalog can still use it.
    weakref.finalize(snapshot, shutil.rmtree, root, True)
    sweep_run_roots()
    return snapshot


def _remote_snapshot() -> SkillCatalogSnapshot | None:
    candidates = [release for release in stored_releases() if release.seq not in _rejected]
    if not candidates:
        return None
    required = _bundled_names()
    for release in candidates:
        snapshot = _release_snapshot(release, required)
        if snapshot is not None:
            return snapshot
    return None


def load_snapshot() -> SkillCatalogSnapshot:
    """Build the catalog this process should serve right now."""
    override = override_root()
    if override is not None:
        snapshot = build_snapshot(override, source=SkillSource.OVERRIDE, release="override")
        return replace(snapshot, release=_catalog_release(snapshot, "override"))
    if auto_update_enabled():
        remote = _remote_snapshot()
        if remote is not None:
            return remote
    return build_snapshot(
        files.skills_dir(),
        source=SkillSource.BUNDLED,
        release=f"bundled:{get_opensre_version()}",
    )


def forget_rejections() -> None:
    """Allow previously rejected releases to be evaluated again (tests, key rotation)."""
    with _rejected_lock:
        _rejected.clear()


__all__ = [
    "auto_update_enabled",
    "forget_rejections",
    "load_snapshot",
    "override_root",
    "source_stamp",
]
