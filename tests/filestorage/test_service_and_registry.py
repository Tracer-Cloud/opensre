"""Shared service + provider registry contracts used by all surfaces."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from config.constants.filestorage import (
    DEFAULT_MAX_PARALLEL_UPLOADS,
    REMOTE_SYNC_BUCKET_ENV,
    REMOTE_SYNC_ENV,
    REMOTE_SYNC_PROVIDER_ENV,
)
from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.contracts import RemoteObject
from infrastructure.filestorage.engine import SyncReport, content_tag
from infrastructure.filestorage.enums import RemoteSyncField, SyncDirection, SyncRootName
from infrastructure.filestorage.errors import RemoteSyncConfigError
from infrastructure.filestorage.messages import (
    DISABLED_HELP,
    direction_label,
    format_report_lines,
    format_status_lines,
    sanitize_terminal_text,
)
from infrastructure.filestorage.operations import get_sync_status, run_remote_sync
from infrastructure.filestorage.providers import build_object_store as surface_build
from infrastructure.filestorage.providers.registry import (
    SetupExtraField,
    build_object_store,
    max_parallel_uploads_for_provider,
    provider_extra_fields,
    register_object_store,
    registered_providers,
    unregister_object_store,
)
from infrastructure.filestorage.syncable import SyncRoot


class _MemStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        from datetime import UTC, datetime

        return [
            RemoteObject(
                key=k,
                size=len(v),
                last_modified=datetime.now(tz=UTC),
                etag=content_tag(v),
            )
            for k, v in self.objects.items()
            if k.startswith(prefix)
        ]

    def get_object(self, key: str) -> bytes:
        return self.objects[key]

    def put_object(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def describe(self) -> str:
        return "mem://t"


@pytest.fixture(autouse=True)
def _restore_registry() -> Iterator[None]:
    from infrastructure.filestorage.providers import registry as reg

    with reg._REGISTRY_LOCK:
        # Import every built-in before snapshotting. Restoring an empty
        # registry after a lazy import leaves the module cached but its
        # import-time registration missing for later tests.
        for provider in reg._BUILTIN_MODULES:
            reg._load_builtin(provider)
        snap = dict(reg._REGISTRY)
        # Restored alongside the factories: a declared upload cap outliving its
        # registration would silently re-tune a later test's push.
        caps = dict(reg._MAX_PARALLEL_UPLOADS)
    yield
    with reg._REGISTRY_LOCK:
        reg._REGISTRY.clear()
        reg._REGISTRY.update(snap)
        reg._MAX_PARALLEL_UPLOADS.clear()
        reg._MAX_PARALLEL_UPLOADS.update(caps)


def test_formatters_return_immutable_tuples() -> None:
    off = format_status_lines(get_sync_status())
    assert isinstance(off, tuple)
    assert DISABLED_HELP in off

    report = SyncReport(uploaded=["a"], downloaded=["b"], kept_remote=["c"], skipped=2)
    lines = format_report_lines(report)
    assert isinstance(lines, tuple)
    assert lines[0] == "Sync complete — 1 downloaded, 1 uploaded, 2 already current."
    report.uploaded.append("mutated")
    # Prior tuple is unchanged; a new format call snapshots the mutated lists.
    assert lines[0] == "Sync complete — 1 downloaded, 1 uploaded, 2 already current."
    assert "2 uploaded" in format_report_lines(report)[0]


def test_format_report_lines_marks_a_dry_run_as_a_preview() -> None:
    report = SyncReport(uploaded=["a"], downloaded=["b"], skipped=2)
    lines = format_report_lines(report, dry_run=True)
    assert lines[0] == "Dry run — 1 would be downloaded, 1 would be uploaded, 2 already current."


def test_format_report_lines_shows_nonzero_byte_sizes() -> None:
    report = SyncReport(
        uploaded=["a"],
        downloaded=["b"],
        uploaded_bytes=4500,
        downloaded_bytes=1200,
        skipped=2,
    )
    lines = format_report_lines(report)
    assert "4.4 KiB" in lines[0]
    assert "1.2 KiB" in lines[0]
    assert "5.6 KiB total" in lines[0]


def test_format_report_lines_omits_size_when_no_bytes_moved() -> None:
    report = SyncReport(
        uploaded=["a"], downloaded=[], uploaded_bytes=0, downloaded_bytes=0, skipped=1
    )
    lines = format_report_lines(report)
    assert lines[0] == "Sync complete — 0 downloaded, 1 uploaded, 1 already current."


def test_format_report_lines_shows_only_download_size_when_upload_is_zero() -> None:
    report = SyncReport(
        uploaded=[], downloaded=["b"], uploaded_bytes=0, downloaded_bytes=2048, skipped=1
    )
    lines = format_report_lines(report)
    assert "2.0 KiB" in lines[0]
    assert "2.0 KiB total" in lines[0]


def test_format_report_lines_shows_small_byte_count_in_bytes() -> None:
    report = SyncReport(
        uploaded=["a"], downloaded=[], uploaded_bytes=500, downloaded_bytes=0, skipped=0
    )
    lines = format_report_lines(report)
    assert "500 B" in lines[0]
    assert "500 B total" in lines[0]


def test_format_report_lines_handles_megabyte_boundary() -> None:
    report = SyncReport(
        uploaded=[], downloaded=[], uploaded_bytes=0, downloaded_bytes=2_500_000, skipped=3
    )
    lines = format_report_lines(report)
    assert "2.4 MiB" in lines[0]
    assert "2.4 MiB total" in lines[0]


def test_direction_label_is_distinct_and_shared_by_both_surfaces() -> None:
    """One source of truth for the label, so CLI and REPL cannot drift apart."""
    assert direction_label(SyncDirection.PULL) == "Pulling"
    assert direction_label(SyncDirection.PUSH) == "Pushing"
    assert direction_label(SyncDirection.PULL) != direction_label(SyncDirection.PUSH)


def test_sanitize_terminal_text_leaves_ordinary_paths_untouched() -> None:
    assert sanitize_terminal_text("sessions/a.jsonl") == "sessions/a.jsonl"
    assert sanitize_terminal_text("sessions/日本語.jsonl") == "sessions/日本語.jsonl"


def test_sanitize_terminal_text_replaces_escape_and_control_characters() -> None:
    # ESC (CSI/OSC lead-in), a C0 control char, and a C1 control char.
    crafted = "sessions/\x1b[2K\x1b]0;pwned\x07\x07\x9bwhoami.jsonl"
    cleaned = sanitize_terminal_text(crafted)
    assert "\x1b" not in cleaned
    assert "\x07" not in cleaned
    assert "\x9b" not in cleaned
    assert "sessions/" in cleaned
    assert "whoami.jsonl" in cleaned


def test_format_report_lines_sanitizes_kept_remote_keys() -> None:
    report = SyncReport(kept_remote=["sessions/\x1b[2Kspoofed.jsonl"])
    lines = format_report_lines(report)
    assert not any("\x1b" in line for line in lines)


def test_factory_delegates_to_registry() -> None:
    store = _MemStore()
    register_object_store("factory-test", lambda _cfg: store)
    built = surface_build(RemoteSyncConfig(bucket="b", provider="factory-test"))
    assert built is store


def test_registry_unregister_and_unknown() -> None:
    register_object_store("temp-prov", lambda _cfg: _MemStore())
    assert "temp-prov" in registered_providers()
    unregister_object_store("temp-prov")
    with pytest.raises(RemoteSyncConfigError, match="unknown remote-sync provider"):
        build_object_store(RemoteSyncConfig(bucket="b", provider="temp-prov"))


def test_aws_declares_region_and_profile() -> None:
    # Loading the built-in aws module (via provider_extra_fields) must not
    # require boto3 to have built a client — this only reads the declaration.
    fields = {extra.field for extra in provider_extra_fields("aws")}
    assert fields == {RemoteSyncField.REGION, RemoteSyncField.PROFILE}


def test_vercel_declares_no_extra_fields() -> None:
    assert provider_extra_fields("vercel") == ()


def test_unknown_provider_declares_no_extra_fields() -> None:
    assert provider_extra_fields("does-not-exist") == ()


def test_provider_extra_fields_registered_and_cleaned_up() -> None:
    register_object_store(
        "extra-fields-test",
        lambda _cfg: _MemStore(),
        extra_fields=(SetupExtraField(RemoteSyncField.REGION, "Region"),),
    )
    assert provider_extra_fields("extra-fields-test") == (
        SetupExtraField(RemoteSyncField.REGION, "Region"),
    )
    unregister_object_store("extra-fields-test")
    assert provider_extra_fields("extra-fields-test") == ()


def test_aws_declares_a_wider_upload_cap_than_the_default() -> None:
    # boto3 retries throttling and S3 absorbs the concurrency, so this backend
    # opts above the cautious shared default rather than inheriting it.
    assert max_parallel_uploads_for_provider("aws") > DEFAULT_MAX_PARALLEL_UPLOADS


def test_backends_without_write_retry_inherit_the_cautious_default() -> None:
    # gcs/vercel/azure all raise on the first non-2xx with no retry, so a wide
    # fan-out would turn one throttled write into a failed push.
    for provider in ("gcs", "vercel", "azure"):
        assert max_parallel_uploads_for_provider(provider) == DEFAULT_MAX_PARALLEL_UPLOADS


def test_an_unknown_provider_gets_the_default_cap() -> None:
    # A backend nobody has profiled is never pushed harder than the default.
    assert max_parallel_uploads_for_provider("does-not-exist") == DEFAULT_MAX_PARALLEL_UPLOADS


def test_a_provider_declares_its_own_upload_cap_and_it_is_cleaned_up() -> None:
    register_object_store("cap-test", lambda _cfg: _MemStore(), max_parallel_uploads=7)
    assert max_parallel_uploads_for_provider("cap-test") == 7
    unregister_object_store("cap-test")
    assert max_parallel_uploads_for_provider("cap-test") == DEFAULT_MAX_PARALLEL_UPLOADS


def test_re_registering_without_a_cap_drops_the_previous_one() -> None:
    # Registration is a full replacement, so a stale cap must not survive it.
    register_object_store("cap-reset", lambda _cfg: _MemStore(), max_parallel_uploads=9)
    register_object_store("cap-reset", lambda _cfg: _MemStore())
    assert max_parallel_uploads_for_provider("cap-reset") == DEFAULT_MAX_PARALLEL_UPLOADS
    unregister_object_store("cap-reset")


def test_a_cap_below_one_is_rejected_at_registration() -> None:
    # Zero workers would mean a push that silently transfers nothing.
    with pytest.raises(ValueError, match="at least 1"):
        register_object_store("cap-zero", lambda _cfg: _MemStore(), max_parallel_uploads=0)


def test_the_shared_service_hands_the_engine_the_providers_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap must actually reach ``push``, not just sit in the registry."""
    from infrastructure.filestorage import operations as sync_service

    seen: dict[str, object] = {}

    def _capturing_run_sync(_store: object, **kwargs: object) -> SyncReport:
        seen.update(kwargs)
        return SyncReport()

    register_object_store("cap-wiring", lambda _cfg: _MemStore(), max_parallel_uploads=11)
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "b")
    monkeypatch.setenv(REMOTE_SYNC_PROVIDER_ENV, "cap-wiring")
    monkeypatch.setattr(sync_service, "syncable_roots", tuple)
    monkeypatch.setattr(sync_service, "run_sync", _capturing_run_sync)

    run_remote_sync()

    assert seen["max_parallel_uploads"] == 11
    unregister_object_store("cap-wiring")


def test_registry_safe_under_concurrent_register_and_build() -> None:
    def _work(i: int) -> str:
        name = f"conc-{i % 4}"
        register_object_store(name, lambda _cfg: _MemStore())
        store = build_object_store(RemoteSyncConfig(bucket="b", provider=name))
        assert store.describe() == "mem://t"
        return name

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_work, i) for i in range(32)]
        assert len({f.result() for f in as_completed(futs)}) == 4


def test_run_remote_sync_uses_scoped_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from infrastructure.filestorage import operations as sync_service

    sessions = tmp_path / "sessions"
    memory = tmp_path / "memory"
    sessions.mkdir()
    memory.mkdir()
    (sessions / "x.jsonl").write_text("{}\n", encoding="utf-8")
    roots = (
        SyncRoot(name=SyncRootName.SESSIONS, path=sessions),
        SyncRoot(name=SyncRootName.MEMORY, path=memory),
    )
    register_object_store("scoped", lambda _cfg: _MemStore())
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "b")
    monkeypatch.setenv(REMOTE_SYNC_PROVIDER_ENV, "scoped")
    monkeypatch.setattr(sync_service, "syncable_roots", lambda: roots)

    report = run_remote_sync()
    assert report is not None
    assert "sessions/x.jsonl" in report.uploaded
    # Service does not cache reports — each call returns a fresh object.
    report2 = run_remote_sync()
    assert report2 is not None
    assert report2 is not report
