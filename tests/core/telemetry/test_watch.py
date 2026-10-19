"""Incident lifecycle, replay and delivery safety for the opt-in telemetry watch."""

from pathlib import Path

import pytest
from filelock import FileLock, Timeout

from core.telemetry.models import Measurement, MetricMapping, Scope, WatchRule
from core.telemetry.storage.store import WatchStore
from core.telemetry.watch import WatchRunner


class Reader:
    def __init__(self) -> None:
        self.errors = 20.0
        self.total = 20.0
        self.fresh = True

    def measure(self, scope: Scope, _mapping: MetricMapping) -> Measurement:
        return Measurement(
            total=self.total,
            errors=self.errors,
            latest=scope.end if self.fresh else None,
        )

    def evidence(self, _scope: Scope) -> dict[str, list[dict[str, object]]]:
        return {"traces": [{"id": "trace-1", "error": "Invalid token"}], "logs": []}


def test_replay_restart_missing_data_and_delivery_retry(tmp_path: Path) -> None:
    rule = WatchRule(service="payment", environment="demo", healthy_windows=2)
    reader = Reader()
    reports: list[dict] = []
    deliveries: list[str] = []
    deliver_ok = False

    def investigate(scope, measurement, evidence):
        reports.append(evidence)
        return "Likely cause: invalid token. Unknown: provider status."

    def deliver(event_id, body):
        deliveries.append(event_id)
        return deliver_ok

    store = WatchStore(tmp_path / "watch.db", "source-and-rule")
    runner = WatchRunner(rule, reader, store, investigate, deliver)
    runner.step(180)
    runner.step(180)  # Same completed window must not duplicate an incident.
    assert len(reports) == 1
    assert len(store.events()) == 1
    assert not store.events()[0]["delivered"]
    deliver_ok = True
    # A fresh process retries only delivery, never a completed model call.
    runner = WatchRunner(
        rule, reader, WatchStore(store.path, "source-and-rule"), investigate, deliver
    )
    runner.step(210)
    assert len(reports) == 1
    assert store.events()[0]["delivered"]
    reader.errors = 0
    runner.step(240)
    reader.fresh = False
    runner.step(270)  # Missing telemetry breaks the recovery streak.
    assert len(store.events()) == 1
    reader.fresh = True
    runner.step(300)
    runner.step(330)
    assert [event["kind"] for event in store.events()] == ["opened", "recovered"]
    assert len(reports) == 1
    assert all(event["delivered"] for event in store.events())


def test_crash_after_detection_replays_pending_investigation(tmp_path: Path) -> None:
    rule = WatchRule(service="payment", environment="demo")
    store = WatchStore(tmp_path / "watch.db", "same")
    scope = rule.scope(180)
    store.observe(scope, "bad", rule, Measurement(20, 20, scope.end))
    assert store.pending() is not None
    calls: list[str] = []

    def investigate(scope, measurement, evidence):
        calls.append(scope.service)
        return "Retained after restart"

    runner = WatchRunner(
        rule, Reader(), WatchStore(store.path, "same"), investigate, lambda *_: True
    )
    runner.step(210)
    assert calls == ["payment"]
    assert store.events()[0]["body"].endswith("Retained after restart")


def test_scope_change_and_concurrent_watch_are_rejected(tmp_path: Path) -> None:
    store = WatchStore(tmp_path / "watch.db", "scope-a")
    with pytest.raises(ValueError, match="different watch"):
        WatchStore(store.path, "scope-b")
    rule = WatchRule(service="payment", environment="demo")
    runner = WatchRunner(rule, Reader(), store, lambda *_: "report", lambda *_: True)
    with FileLock(str(store.path) + ".lock"), pytest.raises(Timeout):
        runner.step(180)


def test_invalid_counts_and_gaps_never_recover(tmp_path: Path) -> None:
    rule = WatchRule(service="payment", environment="demo")
    store = WatchStore(tmp_path / "watch.db", "same")
    reader = Reader()
    runner = WatchRunner(rule, reader, store, lambda *_: "report", lambda *_: True)
    runner.step(180)
    reader.total = float("nan")
    reader.errors = 0
    assert runner.step(210) == "unknown"
    reader.total = 20
    runner.step(240)
    runner.step(330)  # Missing windows cannot be counted as consecutive healthy.
    assert len(store.events()) == 1
    runner.step(360)
    assert len(store.events()) == 2


def test_concurrent_startup_preserves_completed_report_and_measurement(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "state.db"
    with ThreadPoolExecutor(max_workers=4) as pool:
        stores = list(pool.map(lambda _: WatchStore(path, "same"), range(4)))
    store = stores[0]
    rule = WatchRule(service="payment", environment="demo")
    scope = rule.scope(180)
    original = Measurement(20, 20, scope.end)
    store.observe(scope, "bad", rule, original)
    pending = WatchStore(path, "same").pending()
    assert pending is not None and pending[2] == original
    store.save_report(pending[0], scope, "retained")
    store.save_report(pending[0], scope, "must not replace")
    assert len(WatchStore(path, "same").events()) == 1
    assert store.events()[0]["body"].endswith("retained")


def test_polling_deadlines_allow_ten_healthy_windows_despite_query_latency(tmp_path: Path) -> None:
    from core.telemetry.watch import poll

    rule = WatchRule(service="payment", environment="demo", healthy_windows=10)
    store = WatchStore(tmp_path / "watch.db", "same")
    scope = rule.scope(180)
    store.observe(scope, "bad", rule, Measurement(20, 20, scope.end))
    pending = store.pending()
    assert pending is not None
    store.save_report(pending[0], scope, "report")
    reader = Reader()
    reader.errors = 0
    clock = [210.0]
    original = reader.measure

    def slow_measure(scope: Scope, mapping: MetricMapping) -> Measurement:
        clock[0] += 4
        return original(scope, mapping)

    reader.measure = slow_measure

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    runner = WatchRunner(rule, reader, store, lambda *_: "unused", lambda *_: True)
    ticks = poll(runner, once=False, now=lambda: clock[0], monotonic=lambda: clock[0], sleep=sleep)
    for _ in range(10):
        assert next(ticks) == "healthy"
    assert [event["kind"] for event in store.events()] == ["opened", "recovered"]
