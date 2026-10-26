"""Mirror conversation history and memory between a laptop and a bucket.

Additive on both sides: a file present in one place and missing in the other is
copied, never deleted. When both sides changed, the more recently written one
wins — in both directions, so a push from a laptop that has been offline cannot
replace newer work in the bucket any more than a stale pull can overwrite a
newer local edit. Nothing here removes a session or a memory.

Sessions are append-mostly JSONL and memory files are small markdown, so whole
objects are transferred rather than ranges. Downloads land through a temporary
file and an atomic rename, matching how every local store writes, so an
interrupted pull cannot leave a half-written session behind.

Uploads overlap, up to a cap the provider declares (see
:func:`~infrastructure.filestorage.providers.registry.max_parallel_uploads_for_provider`),
because a push is latency-bound: the laptop spends nearly all of it waiting on
one round trip at a time. The concurrency is confined to the transfer half —
see :func:`push` for why the two halves cannot be interleaved.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from config.constants.filestorage import DEFAULT_MAX_PARALLEL_UPLOADS
from infrastructure.filestorage.contracts import ObjectStore, RemoteObject
from infrastructure.filestorage.enums import SyncDirection
from infrastructure.filestorage.errors import RemoteSyncConfigError, UnsyncablePathError
from infrastructure.filestorage.exclusions import NO_EXCLUSIONS, ExclusionRules
from infrastructure.filestorage.syncable import SyncRoot, resolved_roots, syncable_roots

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncProgress:
    """One evaluated candidate, for a live "how much is left" display.

    Fired for every candidate a direction's pass looks at — transferred,
    skipped as unchanged, or held back for a newer remote copy — not only the
    ones that move. A tree that is mostly already in sync would otherwise
    barely advance a caller's progress bar even though the whole tree is
    being scanned, which is the exact "looks hung" symptom this exists to fix.
    ``completed`` is 1-based and counts up to ``total`` within one direction;
    a full sync's pull pass and push pass each start their own count at 1.
    """

    direction: SyncDirection
    key: str
    completed: int
    total: int


#: A callback raising propagates to the caller, same as any other unexpected
#: error during a sync.
ProgressCallback = Callable[[SyncProgress], None]


@dataclass
class SyncReport:
    """What one sync moved, for the surfaces to print and tests to assert on."""

    uploaded: list[str] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    #: Local files left alone because the bucket held a newer copy. A full sync
    #: resolves these by pulling first; a push-only run cannot.
    kept_remote: list[str] = field(default_factory=list)
    skipped: int = 0
    uploaded_bytes: int = 0
    downloaded_bytes: int = 0
    #: Keys neither sent nor fetched because the user's settings hold them
    #: back. A set, not a counter: a two-way sync sees the same excluded file
    #: once on the way up and once on the way down, and reporting it twice
    #: would overstate how much is being held back.
    excluded: set[str] = field(default_factory=set)

    @property
    def changed(self) -> int:
        return len(self.uploaded) + len(self.downloaded)

    @property
    def total_bytes(self) -> int:
        return self.uploaded_bytes + self.downloaded_bytes


class _PushAction(StrEnum):
    """What a push decided about one candidate.

    Private to this module: the surfaces read the outcome off
    :class:`SyncReport`, so this is the wire between a worker thread and the
    single-threaded reduce, not a vocabulary anything outside shares.
    """

    UPLOADED = "uploaded"
    SKIPPED = "skipped"
    KEPT_REMOTE = "kept_remote"


@dataclass(frozen=True)
class _PushOutcome:
    """One candidate's result, carried back from a worker thread.

    Workers return this instead of touching :class:`SyncReport`. Counters like
    ``skipped`` are read-modify-write and would lose updates under threads, and
    appending from whichever upload happened to finish first would make the
    reported order depend on timing.
    """

    key: str
    action: _PushAction
    size: int = 0


def content_tag(data: bytes) -> str:
    """Content tag comparable with an S3 ETag.

    A change detector, not a security control: it answers "are these the same
    bytes the bucket already holds", and S3 defines that tag as MD5.
    """
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def comparable_etag(obj: RemoteObject) -> str:
    """The object's tag when it can be compared, otherwise empty.

    A multipart upload returns a compound tag (``<hash>-<parts>``) that is not
    the MD5 of the content, so it is treated as no tag at all.
    """
    tag = obj.etag.strip('"')
    return "" if not tag or "-" in tag else tag


def resolve_direction(*, pull_only: bool, push_only: bool) -> SyncDirection:
    """Turn the two surface flags into one direction, rejecting both at once."""
    if pull_only and push_only:
        raise RemoteSyncConfigError("choose one of --pull-only or --push-only, not both")
    if pull_only:
        return SyncDirection.PULL
    if push_only:
        return SyncDirection.PUSH
    return SyncDirection.BOTH


def local_files(root: SyncRoot) -> list[Path]:
    """Every file under one root, sorted; empty when the root does not exist."""
    if not root.path.is_dir():
        return []
    return sorted(p for p in root.path.rglob("*") if p.is_file())


def relative_key(root: SyncRoot, path: Path) -> str:
    """Object key for a local path. Public so status counts what a sync would.

    The exclusion patterns are written against this key, so anything reporting
    on them has to derive it exactly the way the transfer does.
    """
    return f"{root.name}/{path.relative_to(root.path).as_posix()}"


def _modified_at(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def _write_atomically(target: Path, data: bytes) -> None:
    """Write through a temporary file in the same directory, then rename."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".part")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def push(
    store: ObjectStore,
    *,
    roots: tuple[SyncRoot, ...] | None = None,
    report: SyncReport | None = None,
    remote: list[RemoteObject] | None = None,
    exclusions: ExclusionRules = NO_EXCLUSIONS,
    dry_run: bool = False,
    on_progress: ProgressCallback | None = None,
    max_parallel_uploads: int = DEFAULT_MAX_PARALLEL_UPLOADS,
) -> SyncReport:
    """Upload local files whose contents differ from the bucket.

    Every candidate is checked before any of them is sent. The two halves
    cannot be interleaved: the deny-list refusal below is a security boundary,
    and uploading while still walking would leave earlier files in the store by
    the time a denied one is found. Concurrency therefore applies only to the
    transfer half, once the plan is closed.

    Transfers run on a small pool — ``max_parallel_uploads`` at once, which the
    caller takes from the provider's own declaration. Results are folded back
    into ``report`` on this thread in plan order, so the report and the
    ``on_progress`` callbacks are identical to a sequential run and neither
    depends on which upload finished first. A failing upload propagates, as
    before; uploads already in flight are allowed to finish, which is safe
    because a sync only ever adds.

    Under ``dry_run`` nothing is sent, and a key ``pull`` already previewed as
    a download (``result.downloaded``, when both share one report) is trusted
    rather than re-compared against the still-unwritten local file — a real
    full sync would have overwritten it first, so re-reading stale bytes here
    would report it as both downloaded and kept back. A previewed key with no
    local file at all (nothing pulled it before) never reaches the scan below,
    so it is counted as skipped separately — a real sync would have written it
    and then found this same scan matching it.
    """
    roots = roots if roots is not None else syncable_roots()
    result = report if report is not None else SyncReport()
    listing = remote if remote is not None else store.list_objects("")
    by_key = {obj.key: obj for obj in listing}
    # Resolve each root once rather than per file: this loop touches every
    # session and memory file on the machine.
    allowed = resolved_roots(roots)
    # Built once, not per file: membership in a loop needs a set, not a list.
    previewed_pulls = set(result.downloaded) if dry_run else frozenset[str]()

    # Check every candidate before uploading any of them. A denied file found
    # halfway through must not leave earlier files already in the store.
    planned: list[tuple[str, Path]] = []
    for root in roots:
        for path in local_files(root):
            # The deny-list runs on every candidate, before the user's patterns
            # and regardless of them. An exclusion may drop a file from the
            # upload; it can never stop this refusal from being raised.
            if not allowed.contains(path):
                # Reaching here means a root pointed somewhere it should not.
                raise UnsyncablePathError(f"refusing to upload {path}")
            key = relative_key(root, path)
            if exclusions.excludes(key):
                result.excluded.add(key)
                continue
            planned.append((key, path))

    total = len(planned)

    def _report(key: str, completed: int) -> None:
        if on_progress is not None:
            on_progress(SyncProgress(SyncDirection.PUSH, key, completed, total))

    def _transfer_one(candidate: tuple[str, Path]) -> _PushOutcome:
        """Evaluate and, when it differs, upload one candidate.

        Runs on a worker thread, so it reads shared state and returns — it
        never touches ``result`` or fires ``on_progress``.
        """
        key, path = candidate
        if key in previewed_pulls:
            return _PushOutcome(key, _PushAction.SKIPPED)
        data = path.read_bytes()
        existing = by_key.get(key)
        if existing is not None:
            if comparable_etag(existing) == content_tag(data):
                return _PushOutcome(key, _PushAction.SKIPPED)
            if existing.last_modified > _modified_at(path):
                # The store holds the more recent write, so uploading would
                # destroy it. Same rule pull applies in the other direction.
                return _PushOutcome(key, _PushAction.KEPT_REMOTE)
        if not dry_run:
            store.put_object(key, data)
        return _PushOutcome(key, _PushAction.UPLOADED, len(data))

    def _record(outcome: _PushOutcome) -> None:
        if outcome.action is _PushAction.UPLOADED:
            result.uploaded.append(outcome.key)
            result.uploaded_bytes += outcome.size
        elif outcome.action is _PushAction.KEPT_REMOTE:
            result.kept_remote.append(outcome.key)
        else:
            result.skipped += 1

    # A dry run sends nothing, so there is no latency to hide and a pool would
    # only add threads. One candidate does not need a pool either.
    workers = min(max_parallel_uploads, total)
    if dry_run or workers <= 1:
        outcomes: Iterable[_PushOutcome] = map(_transfer_one, planned)
        for completed, outcome in enumerate(outcomes, start=1):
            _record(outcome)
            _report(outcome.key, completed)
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="remote-sync-push") as pool:
            # ``map`` yields in submission order, so the fold below stays in
            # plan order however the uploads interleave. It also re-raises the
            # first failure by plan position, not by whichever failed soonest,
            # keeping the error a caller sees deterministic.
            ordered: Iterator[_PushOutcome] = pool.map(_transfer_one, planned)
            for completed, outcome in enumerate(ordered, start=1):
                _record(outcome)
                _report(outcome.key, completed)

    if previewed_pulls:
        # Keys pull previewed but that never had a local file to begin with
        # (a brand-new remote object) never entered the scan above at all.
        planned_keys = {key for key, _ in planned}
        result.skipped += len(previewed_pulls - planned_keys)
    return result


def pull(
    store: ObjectStore,
    *,
    roots: tuple[SyncRoot, ...] | None = None,
    report: SyncReport | None = None,
    remote: list[RemoteObject] | None = None,
    exclusions: ExclusionRules = NO_EXCLUSIONS,
    dry_run: bool = False,
    on_progress: ProgressCallback | None = None,
) -> SyncReport:
    """Download bucket objects missing locally, or newer than the local copy.

    Under ``dry_run`` nothing is fetched or written: the listing already has
    the size needed to report what would move, so previewing costs no request
    beyond the one listing call.
    """
    roots = roots if roots is not None else syncable_roots()
    result = report if report is not None else SyncReport()
    by_name = {root.name: root for root in roots}
    listing = remote if remote is not None else store.list_objects("")
    total = len(listing)

    def _report(key: str, completed: int) -> None:
        if on_progress is not None:
            on_progress(SyncProgress(SyncDirection.PULL, key, completed, total))

    for completed, obj in enumerate(listing, start=1):
        target = _local_path_for(obj, by_name)
        if target is None:
            _report(obj.key, completed)
            continue
        # Excluding a path means it does not belong on this machine, so the
        # pattern holds in both directions: a file another machine still
        # uploads is not pulled back down here.
        if exclusions.excludes(obj.key):
            result.excluded.add(obj.key)
            _report(obj.key, completed)
            continue
        if not _should_download(obj, target):
            result.skipped += 1
            _report(obj.key, completed)
            continue
        if dry_run:
            result.downloaded_bytes += obj.size
            result.downloaded.append(obj.key)
            _report(obj.key, completed)
            continue
        data = store.get_object(obj.key)
        result.downloaded_bytes += len(data)
        _write_atomically(target, data)
        result.downloaded.append(obj.key)
        _report(obj.key, completed)
    return result


def _local_path_for(obj: RemoteObject, by_name: dict[str, SyncRoot]) -> Path | None:
    """Local file for one object key, or None when the key is not ours."""
    head, _, tail = obj.key.partition("/")
    root = by_name.get(head)
    if root is None or not tail:
        logger.debug("[remote-sync] ignoring unrecognised key %s", obj.key)
        return None
    # A key may not climb out of its root.
    candidate = (root.path / tail).resolve()
    if root.path.resolve() not in candidate.parents:
        logger.warning("[remote-sync] refusing key that escapes its root")
        return None
    return candidate


def _should_download(obj: RemoteObject, target: Path) -> bool:
    if not target.exists():
        return True
    tag = comparable_etag(obj)
    if tag and tag == content_tag(target.read_bytes()):
        return False
    # Both sides changed: the more recent write wins.
    return obj.last_modified > _modified_at(target)


def run_sync(
    store: ObjectStore,
    *,
    direction: SyncDirection = SyncDirection.BOTH,
    roots: tuple[SyncRoot, ...] | None = None,
    exclusions: ExclusionRules = NO_EXCLUSIONS,
    dry_run: bool = False,
    on_progress: ProgressCallback | None = None,
    max_parallel_uploads: int = DEFAULT_MAX_PARALLEL_UPLOADS,
) -> SyncReport:
    """Move files in ``direction``. Both ways pulls first, so an offline edit wins.

    The listing is fetched once and shared: a pull changes local files, never
    the bucket, so the push half can reuse it. ``dry_run`` previews the same
    plan without writing anywhere, local or remote. ``on_progress``, when
    given, is called once per key evaluated in the pull pass and then again
    for the push pass — see :data:`ProgressCallback`. ``max_parallel_uploads``
    caps concurrent uploads in the push pass; callers that built the store from
    a config should pass the provider's own declared limit rather than rely on
    the conservative default.
    """
    report = SyncReport()
    listing = store.list_objects("")
    if direction is not SyncDirection.PUSH:
        pull(
            store,
            roots=roots,
            report=report,
            remote=listing,
            exclusions=exclusions,
            dry_run=dry_run,
            on_progress=on_progress,
        )
    if direction is not SyncDirection.PULL:
        push(
            store,
            roots=roots,
            report=report,
            remote=listing,
            exclusions=exclusions,
            dry_run=dry_run,
            on_progress=on_progress,
            max_parallel_uploads=max_parallel_uploads,
        )
    return report


__all__ = [
    "ProgressCallback",
    "SyncDirection",
    "SyncProgress",
    "SyncReport",
    "comparable_etag",
    "content_tag",
    "pull",
    "push",
    "resolve_direction",
    "run_sync",
]
