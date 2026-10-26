"""The cross-process scheduler reload signal is atomic and best-effort."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from infrastructure.scheduling.scheduler import reload_signal


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the signal file into a temp home."""
    monkeypatch.setattr(reload_signal, "OPENSRE_HOME_DIR", tmp_path)
    return tmp_path


def test_request_then_consume_is_a_single_shot(home: Path) -> None:
    # Nothing pending; requesting makes one consume True; then it clears.
    assert reload_signal.consume_scheduler_reload_request() is False
    reload_signal.request_scheduler_reload()
    assert reload_signal.consume_scheduler_reload_request() is True
    assert reload_signal.consume_scheduler_reload_request() is False


def test_request_is_best_effort_on_write_failure(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An unwritable home must not raise — a failed signal cannot fail the store
    # mutation that triggered it (the scheduler resyncs on its next poll/restart).
    def _unwritable(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "write_text", _unwritable)

    reload_signal.request_scheduler_reload()  # must not raise

    assert reload_signal.consume_scheduler_reload_request() is False


def _run_watcher(
    reconcile,
    store: Path,
    *,
    on_error=None,
) -> tuple[threading.Event, threading.Thread]:
    stop = threading.Event()
    watcher = threading.Thread(
        target=reload_signal.watch_and_reconcile,
        args=(stop, reconcile, store),
        kwargs={"poll_seconds": 0.01, "on_error": on_error},
        daemon=True,
    )
    watcher.start()
    return stop, watcher


def test_watch_reconciles_on_a_signal(home: Path, tmp_path: Path) -> None:
    store = tmp_path / "tasks.json"
    store.write_text("[]")
    done = threading.Event()

    def _reconcile() -> None:
        done.set()

    reload_signal.request_scheduler_reload()
    stop, watcher = _run_watcher(_reconcile, store)
    assert done.wait(1.0)  # the signal triggered a reconcile
    stop.set()
    watcher.join(1.0)


def test_watch_reconciles_on_a_store_change_without_a_signal(home: Path, tmp_path: Path) -> None:
    # A dropped signal still converges: the store file's own change triggers it.
    store = tmp_path / "tasks.json"
    store.write_text("[]")
    done = threading.Event()

    def _reconcile() -> None:
        done.set()

    stop, watcher = _run_watcher(_reconcile, store)
    time.sleep(0.03)
    assert not done.is_set()  # idle while nothing changed
    store.write_text('[{"id": "x"}]')  # store changed, no signal
    assert done.wait(1.0)
    stop.set()
    watcher.join(1.0)


def test_watch_is_idle_when_nothing_changes(home: Path, tmp_path: Path) -> None:
    store = tmp_path / "tasks.json"
    store.write_text("[]")
    calls: list[bool] = []

    stop, watcher = _run_watcher(lambda: calls.append(True), store)
    time.sleep(0.05)
    stop.set()
    watcher.join(1.0)

    assert calls == []  # no signal and no store change → no reconcile, no log churn


def test_watch_retries_after_a_failed_reconcile(home: Path, tmp_path: Path) -> None:
    store = tmp_path / "tasks.json"
    store.write_text("[]")
    attempts: list[bool] = []
    errors: list[BaseException] = []

    def _reconcile() -> None:
        attempts.append(True)
        raise RuntimeError("boom")

    stop, watcher = _run_watcher(_reconcile, store, on_error=errors.append)
    time.sleep(0.02)
    store.write_text('[{"id": "x"}]')  # change triggers a reconcile that keeps failing
    time.sleep(0.06)
    stop.set()
    watcher.join(1.0)

    # A failure does not advance the reconciled signature, so it retries.
    assert len(attempts) >= 2
    assert errors
