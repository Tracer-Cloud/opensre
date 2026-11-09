"""Remote context sync: opt-in, what moves, and what must never leave the laptop.

The security property under test is that credentials stay local. Sessions and
memory are the only things that mirror; ``integrations.json`` and the model-key
file are excluded by an allowlist of roots and again by name.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from config.constants.filestorage import (
    REMOTE_SYNC_BUCKET_ENV,
    REMOTE_SYNC_ENV,
    REMOTE_SYNC_EXCLUDE_ENV,
    REMOTE_SYNC_EXCLUDE_OFF_ENV,
    REMOTE_SYNC_PREFIX_ENV,
    REMOTE_SYNC_PROFILE_ENV,
    REMOTE_SYNC_PROVIDER_ENV,
    REMOTE_SYNC_REGION_ENV,
)
from infrastructure.filestorage import engine as sync_module
from infrastructure.filestorage.config import load_remote_sync_config, remote_sync_enabled
from infrastructure.filestorage.contracts import RemoteObject
from infrastructure.filestorage.engine import (
    ProgressCallback,
    SyncProgress,
    content_tag,
    pull,
    push,
    resolve_direction,
    run_sync,
)
from infrastructure.filestorage.enums import SyncDirection, SyncRootName
from infrastructure.filestorage.errors import RemoteSyncConfigError, UnsyncablePathError
from infrastructure.filestorage.syncable import SyncRoot, is_syncable

# Planted in the credential files. If sync ever widens, this string shows up in
# an uploaded object and the assertion below fails loudly.
LEAKED_SECRET = "sk-live-CANARY-must-never-reach-the-bucket"


class FakeObjectStore:
    """In-memory ObjectStore, so the engine is testable without AWS."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.modified: dict[str, datetime] = {}
        self.listings = 0

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        self.listings += 1
        return [
            RemoteObject(
                key=key,
                size=len(data),
                last_modified=self.modified.get(key, datetime.now(tz=UTC)),
                etag=content_tag(data),
            )
            for key, data in self.objects.items()
            if key.startswith(prefix)
        ]

    def get_object(self, key: str) -> bytes:
        return self.objects[key]

    def put_object(self, key: str, data: bytes) -> None:
        self.objects[key] = data
        self.modified.setdefault(key, datetime.now(tz=UTC))

    def describe(self) -> str:
        return "fake://bucket"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A laptop ~/.opensre with sessions, memory, and credential files."""
    (tmp_path / "sessions").mkdir()
    (tmp_path / "memory").mkdir()
    (tmp_path / "sessions" / "abc.jsonl").write_bytes(b'{"turn": 1}\n')
    (tmp_path / "memory" / "a-fact.md").write_bytes(b"remembered\n")
    # Credentials live beside them and must not move.
    (tmp_path / "integrations.json").write_text(
        f'{{"datadog": {{"api_key": "{LEAKED_SECRET}"}}}}', encoding="utf-8"
    )
    (tmp_path / "llm-auth.json").write_text(f'{{"openai": "{LEAKED_SECRET}"}}', encoding="utf-8")
    return tmp_path


@pytest.fixture
def roots(home: Path) -> tuple[SyncRoot, ...]:
    return (
        SyncRoot(name=SyncRootName.SESSIONS, path=home / "sessions"),
        SyncRoot(name=SyncRootName.MEMORY, path=home / "memory"),
    )


# ── Opt-in ──────────────────────────────────────────────────────────────────


def test_sync_is_off_unless_switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: a bucket is named but the switch is not set.
    monkeypatch.delenv(REMOTE_SYNC_ENV, raising=False)
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "left-over-bucket")

    # Act / Assert: naming a bucket must not start uploading.
    assert remote_sync_enabled() is False
    assert load_remote_sync_config() is None


def test_switched_on_without_a_bucket_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.delenv(REMOTE_SYNC_BUCKET_ENV, raising=False)

    # Act / Assert
    with pytest.raises(RemoteSyncConfigError):
        load_remote_sync_config()


def test_prefix_scopes_the_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv(REMOTE_SYNC_ENV, "yes")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "my-bucket")
    monkeypatch.setenv(REMOTE_SYNC_PREFIX_ENV, "laptop-1")

    # Act
    config = load_remote_sync_config()

    # Assert
    assert config is not None
    assert config.key_for("sessions/abc.jsonl") == "laptop-1/sessions/abc.jsonl"


# ── Credentials never leave the laptop ──────────────────────────────────────


def test_credential_files_are_never_uploaded(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    """The canary secret must appear in no uploaded object."""
    # Arrange
    store = FakeObjectStore()

    # Act
    push(store, roots=roots)

    # Assert: something was uploaded, and none of it carries the secret.
    assert store.objects, "expected sessions and memory to upload"
    for key, body in store.objects.items():
        assert LEAKED_SECRET.encode() not in body, f"secret leaked into {key}"
    assert not any("integrations" in key or "llm-auth" in key for key in store.objects)


def test_credential_paths_are_not_syncable(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    # Arrange / Act / Assert
    assert is_syncable(home / "sessions" / "abc.jsonl", roots=roots) is True
    assert is_syncable(home / "memory" / "a-fact.md", roots=roots) is True
    assert is_syncable(home / "integrations.json", roots=roots) is False
    assert is_syncable(home / "llm-auth.json", roots=roots) is False


def test_a_root_pointing_at_credentials_is_refused(home: Path) -> None:
    """Defence in depth: a misconfigured root raises, and leaks nothing first.

    The root allowlist is the primary defence — credential files are normally
    never enumerated. This covers the case where that structure is wrong, which
    is the only way the name check can be reached.
    """
    # Arrange: a root that wrongly covers the whole home directory.
    bad_roots = (SyncRoot(name="everything", path=home),)
    store = FakeObjectStore()

    # Act
    with pytest.raises(UnsyncablePathError):
        push(store, roots=bad_roots)

    # Assert: it stopped before the secret went anywhere.
    for key, body in store.objects.items():
        assert LEAKED_SECRET.encode() not in body, f"secret leaked into {key}"


# ── Moving files ────────────────────────────────────────────────────────────


def test_push_then_pull_restores_a_second_machine(
    home: Path, roots: tuple[SyncRoot, ...], tmp_path: Path
) -> None:
    # Arrange: machine one uploads.
    store = FakeObjectStore()
    push(store, roots=roots)

    # Act: machine two starts empty and pulls.
    second = tmp_path / "machine-two"
    second_roots = (
        SyncRoot(name=SyncRootName.SESSIONS, path=second / "sessions"),
        SyncRoot(name=SyncRootName.MEMORY, path=second / "memory"),
    )
    report = pull(store, roots=second_roots)

    # Assert
    assert sorted(report.downloaded) == ["memory/a-fact.md", "sessions/abc.jsonl"]
    assert (second / "sessions" / "abc.jsonl").read_text(encoding="utf-8") == '{"turn": 1}\n'
    assert (second / "memory" / "a-fact.md").read_text(encoding="utf-8") == "remembered\n"


def test_unchanged_files_are_skipped_not_reuploaded(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    # Arrange
    store = FakeObjectStore()
    push(store, roots=roots)

    # Act: a second push with nothing changed.
    report = push(store, roots=roots)

    # Assert
    assert report.uploaded == []
    assert report.skipped == 2


def test_newer_remote_wins_over_older_local(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    # Arrange: remote holds a newer edit of a file that also exists locally.
    store = FakeObjectStore()
    newer = b'{"turn": 2}\n'
    store.put_object("sessions/abc.jsonl", newer)
    store.modified["sessions/abc.jsonl"] = datetime.now(tz=UTC) + timedelta(hours=1)

    # Act
    pull(store, roots=roots)

    # Assert
    assert (home / "sessions" / "abc.jsonl").read_bytes() == newer


def test_older_remote_does_not_clobber_a_newer_local_edit(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    # Arrange: remote is stale relative to the local file.
    store = FakeObjectStore()
    stale = b'{"turn": 0}\n'
    store.put_object("sessions/abc.jsonl", stale)
    store.modified["sessions/abc.jsonl"] = datetime.now(tz=UTC) - timedelta(hours=1)

    # Act
    pull(store, roots=roots)

    # Assert: the local edit survives.
    assert (home / "sessions" / "abc.jsonl").read_bytes() == b'{"turn": 1}\n'


def test_dry_run_preview_matches_what_a_real_full_sync_would_settle_on(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    """A full sync pulls the newer remote copy, then push sees it already matches.

    A dry run must land on the same final classification — downloaded, and
    neither uploaded nor kept back — without ever writing the file, so push
    cannot be misled by comparing against the still-stale bytes on disk.
    """
    # Arrange: remote holds a newer edit of a file that also exists locally.
    store = FakeObjectStore()
    newer = b'{"turn": 2}\n'
    store.put_object("sessions/abc.jsonl", newer)
    store.modified["sessions/abc.jsonl"] = datetime.now(tz=UTC) + timedelta(hours=1)

    # Act
    report = run_sync(store, roots=roots, dry_run=True)

    # Assert
    assert report.downloaded == ["sessions/abc.jsonl"]
    assert "sessions/abc.jsonl" not in report.uploaded
    assert "sessions/abc.jsonl" not in report.kept_remote
    assert (home / "sessions" / "abc.jsonl").read_bytes() == b'{"turn": 1}\n'
    assert store.objects["sessions/abc.jsonl"] == newer


def test_dry_run_writes_nothing_locally_or_remotely(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    # Arrange: one file only the bucket knows about, one only the laptop knows about.
    store = FakeObjectStore()
    store.put_object("memory/from-remote.md", b"hello\n")
    objects_before = dict(store.objects)

    # Act
    report = run_sync(store, roots=roots, dry_run=True)

    # Assert: reported as it would happen, but nothing actually moved.
    assert report.downloaded == ["memory/from-remote.md"]
    assert sorted(report.uploaded) == ["memory/a-fact.md", "sessions/abc.jsonl"]
    assert report.downloaded_bytes == len(b"hello\n")
    assert store.objects == objects_before
    # A real full sync would write this file, then have push's local-file scan
    # find it and count it skipped — the preview must match that tally even
    # though the brand-new file was never written and so never entered the scan.
    assert report.skipped == 1


def test_dry_runs_skipped_tally_matches_a_real_sync_for_a_brand_new_remote_file(
    home: Path, roots: tuple[SyncRoot, ...], tmp_path: Path
) -> None:
    """The preview's counts must agree with what running for real would produce."""
    # Arrange: two identical starting points, one bucket shared between them.
    store = FakeObjectStore()
    store.put_object("memory/from-remote.md", b"hello\n")
    real_home = tmp_path / "real"
    (real_home / "sessions").mkdir(parents=True)
    (real_home / "memory").mkdir(parents=True)
    (real_home / "sessions" / "abc.jsonl").write_text('{"turn": 1}\n', encoding="utf-8")
    (real_home / "memory" / "a-fact.md").write_text("remembered\n", encoding="utf-8")
    real_roots = (
        SyncRoot(name=SyncRootName.SESSIONS, path=real_home / "sessions"),
        SyncRoot(name=SyncRootName.MEMORY, path=real_home / "memory"),
    )

    # Act
    dry = run_sync(store, roots=roots, dry_run=True)
    real = run_sync(store, roots=real_roots)

    # Assert
    assert dry.skipped == real.skipped
    assert sorted(dry.uploaded) == sorted(real.uploaded)
    assert dry.downloaded == real.downloaded
    assert not (home / "memory" / "from-remote.md").exists()


def test_push_only_dry_run_does_not_upload(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    # Arrange
    store = FakeObjectStore()

    # Act
    report = push(store, roots=roots, dry_run=True)

    # Assert: reported as it would upload, but the store stays empty.
    assert sorted(report.uploaded) == ["memory/a-fact.md", "sessions/abc.jsonl"]
    assert store.objects == {}


# ── Progress ─────────────────────────────────────────────────────────────


def _progress_recorder() -> tuple[list[SyncProgress], ProgressCallback]:
    events: list[SyncProgress] = []
    return events, events.append


def test_push_reports_progress_for_every_candidate_with_a_known_total(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    store = FakeObjectStore()
    events, on_progress = _progress_recorder()

    push(store, roots=roots, on_progress=on_progress)

    assert sorted((e.key, e.direction) for e in events) == [
        ("memory/a-fact.md", SyncDirection.PUSH),
        ("sessions/abc.jsonl", SyncDirection.PUSH),
    ]
    # Both candidates share one total, and completed counts up to it.
    assert {e.total for e in events} == {2}
    assert sorted(e.completed for e in events) == [1, 2]


def test_pull_reports_progress_for_every_candidate_with_a_known_total(
    home: Path, roots: tuple[SyncRoot, ...], tmp_path: Path
) -> None:
    store = FakeObjectStore()
    push(store, roots=roots)
    second_roots = (
        SyncRoot(name=SyncRootName.SESSIONS, path=tmp_path / "two" / "sessions"),
        SyncRoot(name=SyncRootName.MEMORY, path=tmp_path / "two" / "memory"),
    )
    events, on_progress = _progress_recorder()

    pull(store, roots=second_roots, on_progress=on_progress)

    assert sorted((e.key, e.direction) for e in events) == [
        ("memory/a-fact.md", SyncDirection.PULL),
        ("sessions/abc.jsonl", SyncDirection.PULL),
    ]
    assert {e.total for e in events} == {2}
    assert sorted(e.completed for e in events) == [1, 2]


def test_progress_still_fires_for_files_already_in_sync(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    """A second push with nothing changed still reports — the tree was still scanned.

    Reporting only actual transfers would make a bar barely move on a tree
    that is mostly already in sync, which is the exact "looks hung" symptom
    progress reporting exists to fix.
    """
    store = FakeObjectStore()
    push(store, roots=roots)
    events, on_progress = _progress_recorder()

    report = push(store, roots=roots, on_progress=on_progress)

    assert report.uploaded == []
    assert sorted(e.key for e in events) == ["memory/a-fact.md", "sessions/abc.jsonl"]


def test_dry_run_still_reports_progress_for_the_preview(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    """A dry run previews what would move, so progress fires the same as a real run."""
    store = FakeObjectStore()
    events, on_progress = _progress_recorder()

    push(store, roots=roots, dry_run=True, on_progress=on_progress)

    assert sorted((e.key, e.direction) for e in events) == [
        ("memory/a-fact.md", SyncDirection.PUSH),
        ("sessions/abc.jsonl", SyncDirection.PUSH),
    ]
    # Preview only: nothing actually reached the store.
    assert store.objects == {}


def test_run_sync_progress_covers_pull_then_push(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    """A full sync reports the pull half before the push half, matching run order."""
    store = FakeObjectStore()
    store.put_object("memory/from-remote.md", b"hello\n")
    events, on_progress = _progress_recorder()

    run_sync(store, roots=roots, on_progress=on_progress)

    directions_in_order = [e.direction for e in events]
    assert directions_in_order[0] is SyncDirection.PULL
    assert SyncDirection.PUSH in directions_in_order
    assert any(
        e.key == "memory/from-remote.md" and e.direction is SyncDirection.PULL for e in events
    )
    # Each direction's own pass starts its own count at 1, restarting after
    # the pull pass hands off to the push pass.
    pull_completed = [e.completed for e in events if e.direction is SyncDirection.PULL]
    push_completed = [e.completed for e in events if e.direction is SyncDirection.PUSH]
    assert pull_completed[0] == 1
    assert push_completed[0] == 1


def test_progress_reports_a_key_pull_skips_as_outside_any_root(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    """An unrecognised remote key is still one of the candidates pull looked at."""
    store = FakeObjectStore()
    store.put_object("not-a-known-root/whatever.txt", b"data")
    events, on_progress = _progress_recorder()

    pull(store, roots=roots, on_progress=on_progress)

    assert "not-a-known-root/whatever.txt" in {e.key for e in events}


def test_sync_never_deletes(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    """A file only one side knows about survives on both."""
    # Arrange
    store = FakeObjectStore()
    only_remote = b"from the other laptop\n"
    store.put_object("memory/other.md", only_remote)

    # Act
    run_sync(store, roots=roots)

    # Assert: local-only file still uploaded, remote-only file still present.
    assert (home / "memory" / "other.md").exists()
    assert "memory/a-fact.md" in store.objects
    assert "memory/other.md" in store.objects


def test_a_key_escaping_its_root_is_ignored(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    """A hostile key must not write outside the synced directory."""
    # Arrange
    store = FakeObjectStore()
    payload = b"escaped"
    store.put_object("sessions/../../evil.txt", payload)

    # Act
    report = pull(store, roots=roots)

    # Assert
    assert report.downloaded == []
    assert not (home.parent / "evil.txt").exists()


# ── Review fixes: atomicity, deny-list, flags, request count ────────────────


def test_pull_writes_atomically_leaving_no_partial_file(
    home: Path, roots: tuple[SyncRoot, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A download that dies mid-write must not damage the existing session.

    The rename is what makes the swap atomic, so the failure is injected there:
    a direct ``write_bytes`` would already have overwritten the file by this
    point and the original contents would be gone.
    """
    # Arrange: remote holds a newer body that pull will want to install.
    store = FakeObjectStore()
    store.put_object("sessions/abc.jsonl", b'{"turn": 99}\n')
    store.modified["sessions/abc.jsonl"] = datetime.now(tz=UTC) + timedelta(hours=1)

    def failing_replace(_src: str, _dst: object) -> None:
        raise OSError("disk gave up during the rename")

    monkeypatch.setattr(sync_module.os, "replace", failing_replace)

    # Act
    with pytest.raises(OSError, match="disk gave up"):
        pull(store, roots=roots)

    # Assert: the original survives untouched and no temp debris is left.
    assert (home / "sessions" / "abc.jsonl").read_bytes() == b'{"turn": 1}\n'
    assert list((home / "sessions").glob("*.part")) == []


def test_keyring_fallback_secrets_are_denied(home: Path) -> None:
    """The keyring fallback file holds secrets and must never sync."""
    # Arrange
    from config.constants.secrets import CREDENTIAL_FALLBACK_FILENAME

    fallback = home / CREDENTIAL_FALLBACK_FILENAME
    fallback.write_text(LEAKED_SECRET, encoding="utf-8")
    bad_roots = (SyncRoot(name="everything", path=home),)
    store = FakeObjectStore()

    # Act
    with pytest.raises(UnsyncablePathError):
        push(store, roots=bad_roots)

    # Assert
    for key, body in store.objects.items():
        assert LEAKED_SECRET.encode() not in body, f"secret leaked into {key}"


def test_both_direction_flags_are_rejected() -> None:
    """Both surfaces share this resolver, so neither can silently pick one."""
    # Arrange / Act / Assert
    with pytest.raises(RemoteSyncConfigError):
        resolve_direction(pull_only=True, push_only=True)


def test_a_full_sync_lists_the_bucket_once(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    """Pull does not change the bucket, so push reuses the same listing."""
    # Arrange
    store = FakeObjectStore()

    # Act
    run_sync(store, roots=roots)

    # Assert
    assert store.listings == 1


def test_second_push_reuploads_nothing(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    """Objects are compared by the tag the listing already carries."""
    # Arrange
    store = FakeObjectStore()
    push(store, roots=roots)

    # Act
    report = push(store, roots=roots)

    # Assert
    assert report.uploaded == []
    assert report.skipped == 2


def test_aws_failures_name_their_cause() -> None:
    """Sync runs on local surfaces, so the operator must see why it failed."""
    # Arrange: a client whose calls fail the way botocore does.
    from botocore.exceptions import ClientError

    from infrastructure.filestorage.config import RemoteSyncConfig
    from infrastructure.filestorage.errors import RemoteSyncUnavailableError
    from infrastructure.filestorage.providers.aws import S3ObjectStore

    class _Failing:
        def get_paginator(self, _name: str) -> object:
            raise ClientError(
                {
                    "Error": {
                        "Code": "NoSuchBucket",
                        "Message": "The specified bucket does not exist",
                    }
                },
                "ListObjectsV2",
            )

    store = S3ObjectStore(RemoteSyncConfig(bucket="missing"), client=_Failing())

    # Act
    with pytest.raises(RemoteSyncUnavailableError) as caught:
        store.list_objects("")

    # Assert: the AWS reason survives, not just a generic message.
    assert "NoSuchBucket" in str(caught.value)


# ── Edge cases ───────────────────────────────────────────────────────────────


def test_multipart_etag_is_not_treated_as_a_content_match(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    """Compound S3 ETags (``md5-parts``) must not suppress a needed upload."""
    from infrastructure.filestorage.engine import comparable_etag

    # Arrange: listing carries a multipart-style tag that is not content MD5.
    # The object is stamped older than the local file so recency cannot be what
    # decides this — the unusable tag is the only thing under test.
    body = (home / "sessions" / "abc.jsonl").read_bytes()
    store = FakeObjectStore()
    store.put_object("sessions/abc.jsonl", body)
    multipart = RemoteObject(
        key="sessions/abc.jsonl",
        size=len(body),
        last_modified=datetime.now(tz=UTC) - timedelta(hours=1),
        etag=f"{content_tag(body)}-2",
    )
    assert comparable_etag(multipart) == ""

    # Act: push with that listing injected
    report = push(store, roots=roots, remote=[multipart])

    # Assert: file is re-uploaded because the tag was unusable
    assert "sessions/abc.jsonl" in report.uploaded


def test_nested_session_files_round_trip(
    home: Path, roots: tuple[SyncRoot, ...], tmp_path: Path
) -> None:
    # Arrange
    nested = home / "sessions" / "project" / "nested.jsonl"
    nested.parent.mkdir()
    nested.write_text('{"nested": true}\n', encoding="utf-8")
    store = FakeObjectStore()
    push(store, roots=roots)

    # Act
    second = tmp_path / "machine-two"
    second_roots = (
        SyncRoot(name=SyncRootName.SESSIONS, path=second / "sessions"),
        SyncRoot(name=SyncRootName.MEMORY, path=second / "memory"),
    )
    pull(store, roots=second_roots)

    # Assert
    assert (second / "sessions" / "project" / "nested.jsonl").read_text(
        encoding="utf-8"
    ) == '{"nested": true}\n'


def test_empty_roots_are_a_no_op(tmp_path: Path) -> None:
    # Arrange: roots exist but hold no files yet.
    sessions = tmp_path / "sessions"
    memory = tmp_path / "memory"
    sessions.mkdir()
    memory.mkdir()
    roots = (
        SyncRoot(name=SyncRootName.SESSIONS, path=sessions),
        SyncRoot(name=SyncRootName.MEMORY, path=memory),
    )
    store = FakeObjectStore()

    # Act
    report = run_sync(store, roots=roots)

    # Assert
    assert report.uploaded == []
    assert report.downloaded == []
    assert store.objects == {}


def test_unknown_remote_key_prefix_is_ignored(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    # Arrange
    store = FakeObjectStore()
    store.put_object("scratch/notes.txt", b"not ours\n")

    # Act
    report = pull(store, roots=roots)

    # Assert
    assert report.downloaded == []
    assert not (home / "scratch").exists()


def test_push_only_does_not_download(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    from infrastructure.filestorage.engine import SyncDirection

    # Arrange: remote-only file must stay remote-only under push-only.
    store = FakeObjectStore()
    store.put_object("memory/remote-only.md", b"stay remote\n")

    # Act
    report = run_sync(store, direction=SyncDirection.PUSH, roots=roots)

    # Assert
    assert report.downloaded == []
    assert not (home / "memory" / "remote-only.md").exists()
    assert "sessions/abc.jsonl" in report.uploaded


def test_pull_only_does_not_upload(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    from infrastructure.filestorage.engine import SyncDirection

    # Arrange
    store = FakeObjectStore()
    store.put_object("memory/from-remote.md", b"hello\n")

    # Act
    report = run_sync(store, direction=SyncDirection.PULL, roots=roots)

    # Assert
    assert "memory/from-remote.md" in report.downloaded
    assert report.uploaded == []
    assert "sessions/abc.jsonl" not in store.objects


def test_files_outside_allowlisted_roots_are_not_syncable(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    # Arrange: a new top-level file under home is excluded by default.
    stray = home / "notes.md"
    stray.write_text("local only\n", encoding="utf-8")

    # Act / Assert
    assert is_syncable(stray, roots=roots) is False


def test_credentials_json_is_not_syncable(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    from config.constants.secrets import CREDENTIAL_FALLBACK_FILENAME

    # Arrange
    path = home / CREDENTIAL_FALLBACK_FILENAME
    path.write_text(LEAKED_SECRET, encoding="utf-8")

    # Act / Assert
    assert is_syncable(path, roots=roots) is False


def test_comparable_etag_strips_quotes_and_rejects_multipart() -> None:
    from infrastructure.filestorage.engine import comparable_etag

    now = datetime.now(tz=UTC)
    quoted = RemoteObject(key="k", size=1, last_modified=now, etag='"abc123"')
    multipart = RemoteObject(key="k", size=1, last_modified=now, etag="abc123-3")
    empty = RemoteObject(key="k", size=1, last_modified=now, etag="")

    assert comparable_etag(quoted) == "abc123"
    assert comparable_etag(multipart) == ""
    assert comparable_etag(empty) == ""


def test_resolve_direction_maps_each_flag() -> None:
    from infrastructure.filestorage.engine import SyncDirection

    assert resolve_direction(pull_only=False, push_only=False) is SyncDirection.BOTH
    assert resolve_direction(pull_only=True, push_only=False) is SyncDirection.PULL
    assert resolve_direction(pull_only=False, push_only=True) is SyncDirection.PUSH


def test_missing_root_directory_is_skipped_not_fatal(tmp_path: Path) -> None:
    # Arrange: sessions dir was never created on this machine.
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "a.md").write_text("x\n", encoding="utf-8")
    roots = (
        SyncRoot(name=SyncRootName.SESSIONS, path=tmp_path / "sessions"),
        SyncRoot(name=SyncRootName.MEMORY, path=memory),
    )
    store = FakeObjectStore()

    # Act
    report = push(store, roots=roots)

    # Assert
    assert report.uploaded == ["memory/a.md"]
    assert "sessions/" not in "".join(store.objects)


# ── Provider registry (open/closed: new backends register, engine unchanged) ─


def test_s3_is_the_default_registered_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config.constants.filestorage import (
        DEFAULT_REMOTE_SYNC_PROVIDER,
        REMOTE_SYNC_BUCKET_ENV,
        REMOTE_SYNC_ENV,
        REMOTE_SYNC_PROVIDER_ENV,
    )
    from infrastructure.filestorage.providers import registered_providers

    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "b")
    monkeypatch.delenv(REMOTE_SYNC_PROVIDER_ENV, raising=False)

    config = load_remote_sync_config()
    assert config is not None
    assert config.provider == DEFAULT_REMOTE_SYNC_PROVIDER
    assert "aws" in registered_providers()


def test_unknown_provider_fails_closed_at_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config.constants.filestorage import (
        REMOTE_SYNC_BUCKET_ENV,
        REMOTE_SYNC_ENV,
        REMOTE_SYNC_PROVIDER_ENV,
    )
    from infrastructure.filestorage import build_object_store

    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "b")
    monkeypatch.setenv(REMOTE_SYNC_PROVIDER_ENV, "azure-blob")

    config = load_remote_sync_config()
    assert config is not None
    with pytest.raises(RemoteSyncConfigError, match="unknown remote-sync provider"):
        build_object_store(config)


def test_a_new_provider_registers_without_touching_the_engine(
    home: Path, roots: tuple[SyncRoot, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Open/closed: register a fake backend; run_sync still works unchanged."""
    from config.constants.filestorage import (
        REMOTE_SYNC_BUCKET_ENV,
        REMOTE_SYNC_ENV,
        REMOTE_SYNC_PROVIDER_ENV,
    )
    from infrastructure.filestorage import build_object_store
    from infrastructure.filestorage.providers.registry import register_object_store

    # Arrange: a one-off in-memory backend registered under a new name.
    register_object_store("memory", lambda _cfg: FakeObjectStore())
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "ignored")
    monkeypatch.setenv(REMOTE_SYNC_PROVIDER_ENV, "memory")

    # Act
    store = build_object_store(load_remote_sync_config())  # type: ignore[arg-type]
    report = run_sync(store, roots=roots)

    # Assert: engine never knew about "memory" — only ObjectStore.
    assert "sessions/abc.jsonl" in report.uploaded
    assert isinstance(store, FakeObjectStore)


def test_shared_service_status_and_run_are_surface_agnostic(
    home: Path, roots: tuple[SyncRoot, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI / REPL / gateway all call get_sync_status + run_remote_sync."""
    from config.constants.filestorage import REMOTE_SYNC_PROVIDER_ENV
    from infrastructure.filestorage import operations as sync_service
    from infrastructure.filestorage.enums import SyncRootName
    from infrastructure.filestorage.messages import (
        DISABLED_HELP,
        format_report_lines,
        format_status_lines,
    )
    from infrastructure.filestorage.operations import get_sync_status, run_remote_sync
    from infrastructure.filestorage.providers.registry import register_object_store

    monkeypatch.delenv(REMOTE_SYNC_ENV, raising=False)
    off = get_sync_status()
    assert off.enabled is False
    assert DISABLED_HELP in format_status_lines(off)
    assert run_remote_sync() is None

    store = FakeObjectStore()
    register_object_store("svc-memory", lambda _cfg: store)
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "b")
    monkeypatch.setenv(REMOTE_SYNC_PROVIDER_ENV, "svc-memory")
    monkeypatch.setattr(sync_service, "syncable_roots", lambda: roots)

    status = get_sync_status()
    assert status.enabled is True
    assert status.config is not None
    assert status.config.provider == "svc-memory"
    assert any(
        line.startswith("Remote sync is on (svc-memory)") for line in format_status_lines(status)
    )
    assert {root.name for root in status.roots} == {
        SyncRootName.SESSIONS,
        SyncRootName.MEMORY,
    }

    report = run_remote_sync()
    assert report is not None
    assert "sessions/abc.jsonl" in report.uploaded
    assert "downloaded" in format_report_lines(report)[0].lower()


def test_shared_service_is_stateless_and_safe_under_concurrent_calls(
    home: Path, roots: tuple[SyncRoot, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No cached store/config; concurrent status/run/format allocate independently."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from config.constants.filestorage import REMOTE_SYNC_PROVIDER_ENV
    from infrastructure.filestorage import operations as sync_service
    from infrastructure.filestorage.messages import format_report_lines, format_status_lines
    from infrastructure.filestorage.operations import get_sync_status, run_remote_sync
    from infrastructure.filestorage.providers.registry import register_object_store

    register_object_store("svc-concurrent", lambda _cfg: FakeObjectStore())
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "b")
    monkeypatch.setenv(REMOTE_SYNC_PROVIDER_ENV, "svc-concurrent")
    monkeypatch.setattr(sync_service, "syncable_roots", lambda: roots)

    def _one(_: int) -> tuple[bool, int, str]:
        status = get_sync_status()
        lines = format_status_lines(status)
        report = run_remote_sync()
        assert report is not None
        # Mutating the returned report must not affect a fresh format of another.
        report.uploaded.append("poison")
        other = run_remote_sync()
        assert other is not None
        assert "poison" not in other.uploaded
        return status.enabled, len(lines), format_report_lines(report)[0]

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_one, i) for i in range(16)]
        results = [fut.result() for fut in as_completed(futures)]

    assert all(enabled for enabled, _n, _line in results)
    assert len({line for _e, _n, line in results}) == 1


def test_push_only_does_not_clobber_a_newer_remote(home: Path, roots: tuple[SyncRoot, ...]) -> None:
    """A stale laptop pushing alone must not replace newer bucket data."""
    # Arrange: the bucket holds a newer edit than this machine has.
    store = FakeObjectStore()
    newer = b'{"turn": 99, "from": "the other laptop"}\n'
    store.put_object("sessions/abc.jsonl", newer)
    store.modified["sessions/abc.jsonl"] = datetime.now(tz=UTC) + timedelta(hours=1)

    # Act: push without pulling first.
    report = push(store, roots=roots)

    # Assert: the newer remote copy survives and the skip is reported.
    assert store.objects["sessions/abc.jsonl"] == newer
    assert "sessions/abc.jsonl" not in report.uploaded
    assert "sessions/abc.jsonl" in report.kept_remote


def test_an_unusable_tag_still_defers_to_a_newer_remote(
    home: Path, roots: tuple[SyncRoot, ...]
) -> None:
    """Not being able to compare content is no licence to overwrite newer work."""
    # Arrange: compound tag, and the bucket copy is the more recent write.
    body = (home / "sessions" / "abc.jsonl").read_bytes()
    store = FakeObjectStore()
    store.put_object("sessions/abc.jsonl", b"newer remote body\n")
    newer_multipart = RemoteObject(
        key="sessions/abc.jsonl",
        size=len(body),
        last_modified=datetime.now(tz=UTC) + timedelta(hours=1),
        etag=f"{content_tag(body)}-2",
    )

    # Act
    report = push(store, roots=roots, remote=[newer_multipart])

    # Assert: the contested key is left alone (the memory file is absent from
    # the injected listing, so its upload is correct and not what is asserted).
    assert "sessions/abc.jsonl" not in report.uploaded
    assert "sessions/abc.jsonl" in report.kept_remote
    assert store.objects["sessions/abc.jsonl"] == b"newer remote body\n"


def test_settings_come_from_the_file_when_the_environment_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stored settings drive sync, so setup does not require shell exports."""
    # Arrange: a config.yml with a remote_sync section, and no env vars.
    from config.constants import paths
    from config.local_settings import update_section

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    for name in (REMOTE_SYNC_ENV, REMOTE_SYNC_BUCKET_ENV, REMOTE_SYNC_PREFIX_ENV):
        monkeypatch.delenv(name, raising=False)
    update_section(
        "remote_sync",
        {"enabled": True, "bucket": "stored-bucket", "prefix": "stored-prefix"},
    )

    # Act
    config = load_remote_sync_config()

    # Assert
    assert config is not None
    assert config.bucket == "stored-bucket"
    assert config.key_for("sessions/a.jsonl") == "stored-prefix/sessions/a.jsonl"


def test_environment_overrides_the_stored_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A one-off export must be able to redirect a single run."""
    # Arrange
    from config.constants import paths
    from config.local_settings import update_section

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    update_section("remote_sync", {"enabled": True, "bucket": "stored-bucket"})
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "env-bucket")

    # Act
    config = load_remote_sync_config()

    # Assert
    assert config is not None
    assert config.bucket == "env-bucket"


def test_a_malformed_stored_bucket_fails_early_naming_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A YAML list under ``bucket`` must not stringify into a bogus bucket name."""
    # Arrange: `bucket: [my-bucket]` instead of a string.
    from config.constants import paths
    from config.local_settings import update_section

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(REMOTE_SYNC_BUCKET_ENV, raising=False)
    update_section("remote_sync", {"enabled": True, "bucket": ["my-bucket"]})
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")

    # Act / Assert: fails closed, naming the offending key.
    with pytest.raises(RemoteSyncConfigError, match="remote_sync.bucket"):
        load_remote_sync_config()


def test_env_bucket_overrides_a_malformed_stored_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env precedence must hold even when the stored value it shadows is bad."""
    # Arrange
    from config.constants import paths
    from config.local_settings import update_section

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    update_section("remote_sync", {"enabled": True, "bucket": ["my-bucket"]})
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "env-bucket")

    # Act
    config = load_remote_sync_config()

    # Assert: the malformed stored bucket is never read, let alone validated.
    assert config is not None
    assert config.bucket == "env-bucket"


def test_a_malformed_stored_enabled_fails_early_instead_of_silently_disabling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad ``enabled`` value must be reported, not read as falsy and ignored."""
    # Arrange
    from config.constants import paths
    from config.local_settings import update_section

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(REMOTE_SYNC_ENV, raising=False)
    update_section("remote_sync", {"enabled": ["true"], "bucket": "stored-bucket"})

    # Act / Assert
    with pytest.raises(RemoteSyncConfigError, match="remote_sync.enabled"):
        remote_sync_enabled()
    with pytest.raises(RemoteSyncConfigError, match="remote_sync.enabled"):
        load_remote_sync_config()


def test_env_enabled_overrides_a_malformed_stored_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``OPENSRE_REMOTE_SYNC`` must still switch sync off without reading the file."""
    # Arrange
    from config.constants import paths
    from config.local_settings import update_section

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    update_section("remote_sync", {"enabled": ["true"], "bucket": "stored-bucket"})
    monkeypatch.setenv(REMOTE_SYNC_ENV, "0")

    # Act / Assert
    assert remote_sync_enabled() is False
    assert load_remote_sync_config() is None


# ── Org-scoped turns must not sync (keys carry no principal or actor) ────────


def test_org_scoped_turn_refuses_to_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two members of one org would otherwise share every object key."""
    # Arrange
    from config.principal import Actor, Principal, StorageScope
    from config.scope_context import bound_storage_scope
    from infrastructure.filestorage.errors import OrgScopeNotSupportedError
    from infrastructure.filestorage.operations import get_sync_status, run_remote_sync

    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "shared-bucket")
    scope = StorageScope(principal=Principal.org("org_acme"), actor=Actor(id="U_ALICE"))

    # Act / Assert: both entry points fail closed while the scope is bound.
    with bound_storage_scope(scope):
        with pytest.raises(OrgScopeNotSupportedError):
            run_remote_sync()
        with pytest.raises(OrgScopeNotSupportedError):
            get_sync_status()


def test_unbound_laptop_turn_still_syncs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The refusal is scoped to organizations, not a blanket disable."""
    # Arrange
    from config.constants import paths
    from infrastructure.filestorage.operations import get_sync_status

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "my-bucket")

    # Act
    status = get_sync_status()

    # Assert
    assert status.enabled is True


def test_a_denied_file_uploads_nothing_at_all(home: Path) -> None:
    """Validation runs over every candidate before the first upload.

    Two roots: the first holds only syncable files, the second reaches a
    credential file. Uploading as it walks would already have written the first
    root's files by the time the refusal fires, so an empty store is the proof.
    """
    # Arrange
    roots_with_a_bad_one = (
        SyncRoot(name="sessions", path=home / "sessions"),
        SyncRoot(name="everything", path=home),
    )
    store = FakeObjectStore()

    # Act
    with pytest.raises(UnsyncablePathError):
        push(store, roots=roots_with_a_bad_one)

    # Assert: not a single object was written before the refusal.
    assert store.objects == {}


def test_a_corrupt_settings_file_surfaces_as_a_sync_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A damaged config.yml must not escape as an unrelated exception type."""
    # Arrange
    from config.constants import paths

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(REMOTE_SYNC_ENV, raising=False)
    (tmp_path / "config.yml").write_text("just a string, not a mapping", encoding="utf-8")

    # Act / Assert: surfaces catch RemoteSyncError, so it must be one.
    with pytest.raises(RemoteSyncConfigError):
        remote_sync_enabled()


def test_env_only_config_ignores_a_corrupt_settings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A damaged config.yml must not break a run configured purely by env."""
    # Arrange: every setting comes from the environment.
    from config.constants import paths

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    (tmp_path / "config.yml").write_text("not a mapping", encoding="utf-8")
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "env-bucket")
    monkeypatch.setenv(REMOTE_SYNC_PREFIX_ENV, "env-prefix")
    monkeypatch.setenv(REMOTE_SYNC_REGION_ENV, "eu-west-2")
    monkeypatch.setenv(REMOTE_SYNC_PROFILE_ENV, "env-profile")
    monkeypatch.setenv(REMOTE_SYNC_PROVIDER_ENV, "aws")
    # Exclusions are a setting like any other, so "purely by env" now includes
    # them. Left unset, an unreadable stored section falls back to the defaults
    # — see test_a_corrupt_settings_file_defaults_to_no_exclusions_when_env_covers_required.
    monkeypatch.setenv(REMOTE_SYNC_EXCLUDE_ENV, "*.tmp")

    # Act
    config = load_remote_sync_config()

    # Assert: the file was never needed, so its damage is irrelevant.
    assert config is not None
    assert config.bucket == "env-bucket"
    assert config.prefix == "env-prefix"
    assert config.exclude.patterns == ("*.tmp",)


def test_the_two_documented_env_vars_survive_a_corrupt_settings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented env-only setup works with defaults for unset optionals.

    Docs teach enabling sync with just ``OPENSRE_REMOTE_SYNC`` and
    ``OPENSRE_REMOTE_SYNC_BUCKET``; every other value defaults. A corrupt
    ``config.yml`` must not block that happy path, even though the optionals
    would otherwise fall through to the file.
    """
    # Arrange
    from config.constants import paths

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    (tmp_path / "config.yml").write_text("just a string, not a mapping", encoding="utf-8")
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "env-bucket")
    for env in (
        REMOTE_SYNC_PREFIX_ENV,
        REMOTE_SYNC_REGION_ENV,
        REMOTE_SYNC_PROFILE_ENV,
        REMOTE_SYNC_PROVIDER_ENV,
        REMOTE_SYNC_EXCLUDE_ENV,
        REMOTE_SYNC_EXCLUDE_OFF_ENV,
    ):
        monkeypatch.delenv(env, raising=False)

    # Act
    config = load_remote_sync_config()

    # Assert: env provides what is required, defaults cover the rest.
    assert config is not None
    assert config.bucket == "env-bucket"
    assert config.prefix == "opensre"
    assert config.provider == "aws"
    assert config.region == ""
    assert config.profile == ""
    assert config.exclude.patterns == ()


def test_a_corrupt_settings_file_still_fails_when_the_bucket_has_no_env_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable file only blocks sync when the env cannot supply a value.

    With the switch on but the bucket only available in the stored section,
    the settings file is required, so its damage has to reach the surface.
    """
    # Arrange
    from config.constants import paths

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    (tmp_path / "config.yml").write_text("not a mapping", encoding="utf-8")
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.delenv(REMOTE_SYNC_BUCKET_ENV, raising=False)

    # Act / Assert
    with pytest.raises(RemoteSyncConfigError):
        load_remote_sync_config()


def test_list_prefix_is_delimited_so_a_sibling_bucket_path_cannot_match() -> None:
    """Prefix "opensre" must not also sweep in "opensre-backup/"."""
    # Arrange
    from infrastructure.filestorage.config import RemoteSyncConfig
    from infrastructure.filestorage.providers.aws import S3ObjectStore

    seen: dict[str, str] = {}

    class _RecordingPaginator:
        def paginate(self, **kwargs: str) -> list[dict[str, object]]:
            seen["Prefix"] = kwargs["Prefix"]
            return []

    class _Client:
        def get_paginator(self, _name: str) -> _RecordingPaginator:
            return _RecordingPaginator()

    store = S3ObjectStore(RemoteSyncConfig(bucket="b", prefix="opensre"), client=_Client())

    # Act
    store.list_objects("")

    # Assert
    assert seen["Prefix"] == "opensre/"
