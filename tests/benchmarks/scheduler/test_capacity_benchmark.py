"""Contract tests for the deterministic scheduler capacity benchmark."""

from __future__ import annotations

from pathlib import Path

from tests.benchmarks.scheduler.capacity_benchmark import (
    BenchmarkConfig,
    run_benchmark,
    write_report,
)


def test_benchmark_records_every_m1_workload_and_writes_a_versioned_report(
    tmp_path: Path,
) -> None:
    report = run_benchmark(
        BenchmarkConfig(
            fake_delivery_seconds=0.002,
            burst_sizes=(3,),
            sustained_seconds=0.02,
            history_sizes=(10,),
            restart_backlog_size=3,
        )
    )
    output_path = tmp_path / "scheduler-capacity.json"
    write_report(report, output_path)

    assert output_path.exists()
    assert report["schema_version"] == 1
    assert report["scheduler_config"]["max_concurrent_runs"] == 2
    assert report["metrics"]["burst"][0]["peak_in_memory_callbacks"] == 3
    assert [result["load"] for result in report["metrics"]["sustained"]] == [
        "below_capacity",
        "at_capacity",
        "above_capacity",
    ]
    assert report["metrics"]["history"][0]["completed_history_rows"] == 10
    assert report["metrics"]["restart_recovery"]["recovered_runs"] == 3
    assert report["metrics"]["restart_recovery"]["recovery_callback_count"] == 1
    assert report["metrics"]["restart_recovery"]["peak_in_memory_callbacks"] == 1
