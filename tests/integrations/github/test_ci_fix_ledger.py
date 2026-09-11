"""Durability, deduplication, and isolation of completed CI repairs."""

from __future__ import annotations

import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.synchronize import Barrier as ProcessBarrier
from pathlib import Path
from threading import Barrier

import pytest
from filelock import FileLock

from config.constants import CI_FIX_LEDGER_PATH_ENV
from integrations.github.tools.ci_fix import ledger, ledger_store


def _outcome(**changes: object) -> dict[str, object]:
    return {
        "success": True,
        "checks_state": "passed",
        "owner": "Example",
        "repo": "service",
        "target_type": "pr",
        "pr_number": 12,
        "source_head_sha": "a" * 40,
        **changes,
    }


def test_only_verified_identifiable_repairs_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ci_fixes.json"
    monkeypatch.setenv(CI_FIX_LEDGER_PATH_ENV, str(path))
    for changes in (
        {"success": False},
        {"checks_state": None},
        {"checks_state": "failed"},
        {"source_head_sha": ""},
    ):
        ledger.record_ci_fix_outcome(_outcome(**changes))
    assert not path.exists()

    ledger.record_ci_fix_outcome(_outcome())
    ledger.record_ci_fix_outcome(_outcome(owner="example", branch_name="another-attempt"))
    assert ledger.count_ci_fixes() == 1
    ledger.record_ci_fix_outcome(_outcome(source_head_sha="b" * 40))
    ledger.record_ci_fix_outcome(_outcome(pr_number=13))
    for branch in ("Main", "main"):
        ledger.record_ci_fix_outcome(_outcome(target_type="branch", target_branch=branch))
    assert ledger.count_ci_fixes() == 5
    data = json.loads(path.read_text())
    assert len(data["fixes"]) == len(set(data["fixes"])) == 5
    assert all(len(identity) == 64 for identity in data["fixes"])
    assert "Example" not in path.read_text()


def test_failed_save_updates_memory_before_io_and_reconciles_without_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ci_fixes.json"
    counter = ledger.CiFixCounter(path)
    observed: list[int] = []
    stop = counter.subscribe(lambda: observed.append(counter.count()))

    def fail_save(_path: Path, identities: set[str]) -> set[str]:
        assert observed == [1]
        assert counter.count() == 1
        raise OSError("disk full")

    with monkeypatch.context() as patcher:
        patcher.setattr(ledger, "append_fix_ids", fail_save)
        counter.record("a" * 64)
    assert counter.reload() == 1
    assert not path.exists()
    # Another process persisted both the same repair and a different one.
    ledger_store.append_fix_ids(path, {"a" * 64, "b" * 64})
    assert counter.reload() == 2
    counter.record("a" * 64)
    assert counter.count() == 2
    stop()
    stop()
    counter.record("c" * 64)
    assert observed == [1, 2]


def test_corrupt_baseline_stays_zero_until_recovery_preserves_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ci_fixes.json"
    broken = '{"version": 1, "fixes": "not a list"}'
    path.write_text(broken)
    counter = ledger.CiFixCounter(path)

    def fail_save(_path: Path, _identities: set[str]) -> set[str]:
        raise OSError("read-only storage")

    with monkeypatch.context() as patcher:
        patcher.setattr(ledger, "append_fix_ids", fail_save)
        counter.record("a" * 64)
        assert counter.count() == 0
    counter.record("b" * 64)
    assert counter.count() == 2
    assert ledger_store.read_fix_ids(path) == {"a" * 64, "b" * 64}
    preserved = list(tmp_path.glob("ci_fixes.json.corrupt-*"))
    assert len(preserved) == 1
    assert preserved[0].read_text() == broken


def test_future_schema_is_never_replaced(tmp_path: Path) -> None:
    path = tmp_path / "ci_fixes.json"
    future = '{"version": 2, "fixes": []}'
    path.write_text(future)
    counter = ledger.CiFixCounter(path)
    counter.record("a" * 64)
    assert counter.count() == 0
    assert path.read_text() == future
    assert not list(tmp_path.glob("*.corrupt-*"))


def test_failed_atomic_replace_keeps_existing_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ci_fixes.json"
    counter = ledger.CiFixCounter(path)
    counter.record("a" * 64)

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("replace failed")

    with monkeypatch.context() as patcher:
        patcher.setattr(ledger_store.os, "replace", fail_replace)
        counter.record("b" * 64)
    assert counter.count() == 2
    assert ledger.CiFixCounter(path).count() == 1
    assert not list(tmp_path.glob(".ci_fixes.json.*"))
    counter.record("b" * 64)
    assert ledger.CiFixCounter(path).count() == 2


def test_lock_contention_keeps_live_count_and_can_retry(tmp_path: Path) -> None:
    path = tmp_path / "ci_fixes.json"
    counter = ledger.CiFixCounter(path)
    with FileLock(str(path) + ".lock"):
        counter.record("a" * 64)
    assert counter.count() == 1
    assert not path.exists()
    counter.record("a" * 64)
    assert ledger.CiFixCounter(path).count() == 1


def _append_on_worker(path: str, identity: str, barrier: ProcessBarrier) -> None:
    counter = ledger.CiFixCounter(Path(path))
    barrier.wait(timeout=30)
    counter.record(identity)


def test_concurrent_processes_keep_both_repairs(tmp_path: Path) -> None:
    path = tmp_path / "ci_fixes.json"
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    workers = [
        context.Process(target=_append_on_worker, args=(str(path), key * 64, barrier))
        for key in ("a", "b")
    ]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=45)
            assert worker.exitcode == 0
        assert ledger_store.read_fix_ids(path) == {"a" * 64, "b" * 64}
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=5)
            worker.close()


def test_concurrent_local_completions_deduplicate(tmp_path: Path) -> None:
    counter = ledger.CiFixCounter(tmp_path / "ci_fixes.json")
    barrier = Barrier(3)

    def record(identity: str) -> None:
        barrier.wait(timeout=10)
        counter.record(identity)

    with ThreadPoolExecutor(max_workers=3) as workers:
        list(workers.map(record, ("a" * 64, "a" * 64, "b" * 64)))
    assert counter.count() == counter.reload() == 2


def test_deployment_scope_shares_shell_count_but_isolates_other_organizations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config.constants.paths as paths
    from config.principal import Actor, Principal, StorageScope
    from config.scope_context import bound_storage_scope

    monkeypatch.delenv(CI_FIX_LEDGER_PATH_ENV, raising=False)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.setattr(paths, "organization_id", lambda: "org_a")
    shell_counter = ledger.get_ci_fix_counter()
    shell_counter.record("a" * 64)
    with bound_storage_scope(StorageScope(Principal.org("org_a"), Actor("alice"))):
        assert ledger.get_ci_fix_counter() is shell_counter
    with bound_storage_scope(StorageScope(Principal.org("org_b"), Actor("bob"))):
        other = ledger.get_ci_fix_counter()
        assert other.count() == 0
        other.record("b" * 64)
    assert shell_counter.count() == 1
