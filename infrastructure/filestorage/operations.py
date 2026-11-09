"""Remote-sync actions a surface invokes.

CLI (``opensre remote-sync``), interactive shell (``/remote-sync``), and
gateway clients all call here, so none of them re-implements config loading,
store building, or engine ordering. Surfaces own only their own I/O.

**Stateless:** no cached config, ObjectStore, or report. Every call re-reads
env/settings, resolves roots for the current scope, and builds a fresh store.
**Thread-safe:** safe to call concurrently from many turns; results are newly
allocated and not shared. Two syncs of the same roots can still race at the
filesystem / object-store layer (last writer wins) — that is external I/O.

Roots come from ``sessions_dir()`` / ``get_memory_dir()``, so the active
principal scope (laptop home vs org user tree) is already applied.

User-facing wording lives in :mod:`infrastructure.filestorage.messages`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config.scope_context import current_scope
from infrastructure.filestorage.config import RemoteSyncConfig, load_remote_sync_config
from infrastructure.filestorage.engine import (
    ProgressCallback,
    SyncReport,
    local_files,
    relative_key,
    resolve_direction,
    run_sync,
)
from infrastructure.filestorage.enums import SyncDirection, SyncRootName
from infrastructure.filestorage.errors import OrgScopeNotSupportedError
from infrastructure.filestorage.exclusions import NO_EXCLUSIONS, ExclusionRules
from infrastructure.filestorage.exposure import PublicAccessStatus
from infrastructure.filestorage.providers import (
    build_object_store,
    check_bucket_exposure,
    max_parallel_uploads_for_provider,
)
from infrastructure.filestorage.syncable import SyncRoot, syncable_roots


@dataclass(frozen=True)
class SyncRootStatus:
    """One mirrored root as shown to the operator.

    ``excluded`` is how many files under this root the configured patterns
    currently hold back. Reported per root because a count is the only way to
    tell a pattern that matched from one that was mistyped.
    """

    name: SyncRootName | str
    path: Path
    exists: bool
    excluded: int = 0


@dataclass(frozen=True)
class SyncStatus:
    """Whether sync is on and what would move — shared across all surfaces.

    ``exposure`` is ``None`` when sync is off (nothing to check) and a
    :class:`~infrastructure.filestorage.exposure.PublicAccessStatus` otherwise —
    always present, since :func:`~infrastructure.filestorage.providers.check_bucket_exposure`
    itself degrades to ``UNKNOWN`` instead of raising.
    """

    config: RemoteSyncConfig | None
    roots: tuple[SyncRootStatus, ...]
    exposure: PublicAccessStatus | None = None

    @property
    def enabled(self) -> bool:
        return self.config is not None

    @property
    def exclusions(self) -> ExclusionRules:
        """Patterns in force, empty when sync is off or none are configured."""
        return self.config.exclude if self.config is not None else NO_EXCLUSIONS


def _owned_report(report: SyncReport) -> SyncReport:
    """Caller-owned copy so concurrent formatters cannot see later mutation."""
    return SyncReport(
        uploaded=list(report.uploaded),
        downloaded=list(report.downloaded),
        kept_remote=list(report.kept_remote),
        skipped=report.skipped,
        uploaded_bytes=report.uploaded_bytes,
        downloaded_bytes=report.downloaded_bytes,
        excluded=set(report.excluded),
    )


def _refuse_org_scoped_turn() -> None:
    """Fail closed when the caller is an organization member, not a laptop user.

    Object keys carry no principal or actor, so every member of an organization
    would write and read the same keys.
    """
    scope = current_scope()
    if scope is not None and scope.principal.kind == "org":
        raise OrgScopeNotSupportedError(
            "Remote sync mirrors a personal machine. This conversation belongs to "
            "an organization, whose history already persists in the shared context "
            "root, so nothing is mirrored."
        )


def _root_status(root: SyncRoot, exclusions: ExclusionRules) -> SyncRootStatus:
    """Describe one root, counting held-back files only when asked to.

    Counting walks the whole root, so an installation with no patterns — the
    default — pays nothing for a feature it is not using.
    """
    excluded = (
        exclusions.matching(relative_key(root, path) for path in local_files(root))
        if exclusions
        else 0
    )
    return SyncRootStatus(
        name=root.name,
        path=root.path,
        exists=root.path.is_dir(),
        excluded=excluded,
    )


def get_sync_status() -> SyncStatus:
    """Load config, resolve scoped roots, and check whether the store is public.

    The exposure check is the one network call this makes — only when sync
    is on, and only if the provider registered a checker; see
    :func:`~infrastructure.filestorage.providers.check_bucket_exposure`.
    """
    _refuse_org_scoped_turn()
    config = load_remote_sync_config()
    exclusions = config.exclude if config is not None else NO_EXCLUSIONS
    roots = tuple(_root_status(root, exclusions) for root in syncable_roots())
    exposure = check_bucket_exposure(config) if config is not None else None
    return SyncStatus(config=config, roots=roots, exposure=exposure)


def run_remote_sync(
    *,
    pull_only: bool = False,
    push_only: bool = False,
    direction: SyncDirection | None = None,
    dry_run: bool = False,
    on_progress: ProgressCallback | None = None,
) -> SyncReport | None:
    """Pull/push for the current scope. ``None`` when sync is disabled.

    Builds a new ObjectStore per call. Returns a caller-owned report snapshot.
    Prefer ``direction=`` when the caller already has a :class:`SyncDirection`;
    the boolean flags remain for CLI/slash adapters. ``dry_run`` previews the
    plan without uploading, downloading, or writing anything locally.
    ``on_progress``, when given, is called once per key evaluated — the
    single place CLI and slash both get live progress from, so neither
    re-derives it. See :class:`infrastructure.filestorage.engine.SyncProgress`.
    """
    _refuse_org_scoped_turn()
    resolved = (
        direction
        if direction is not None
        else resolve_direction(pull_only=pull_only, push_only=push_only)
    )
    config = load_remote_sync_config()
    if config is None:
        return None
    roots = syncable_roots()
    store = build_object_store(config)
    return _owned_report(
        run_sync(
            store,
            direction=resolved,
            roots=roots,
            exclusions=config.exclude,
            dry_run=dry_run,
            on_progress=on_progress,
            # The backend, not the engine, knows how hard it can be pushed.
            max_parallel_uploads=max_parallel_uploads_for_provider(config.provider),
        )
    )


__all__ = [
    "SyncRootStatus",
    "SyncStatus",
    "get_sync_status",
    "run_remote_sync",
]
