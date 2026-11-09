"""Tests for the evidence mappers on integrations.tracer.tools.tracer_airflow_dag_tool.

These tools have no ``@tool`` ``BaseToolContract``-friendly single entrypoint to
mock uniformly (each wraps a different ``integrations.airflow.config`` fetch
function directly), so coverage here focuses on the mappers themselves --
the ``run()`` bodies are exercised end-to-end by ``tests/e2e/airflow/``.
"""

from __future__ import annotations

from typing import Any

from integrations.tracer.tools.tracer_airflow_dag_tool import (
    get_airflow_dag_runs,
    get_airflow_task_instances,
    get_recent_airflow_failures,
)
from integrations.tracer.tools.tracer_airflow_dag_tool._evidence import (
    map_get_airflow_dag_runs as _map_get_airflow_dag_runs,
)
from integrations.tracer.tools.tracer_airflow_dag_tool._evidence import (
    map_get_airflow_task_instances as _map_get_airflow_task_instances,
)
from integrations.tracer.tools.tracer_airflow_dag_tool._evidence import (
    map_get_recent_airflow_failures as _map_get_recent_airflow_failures,
)


def test_get_recent_airflow_failures_carries_mapper() -> None:
    rt = get_recent_airflow_failures.__opensre_registered_tool__
    assert rt.evidence_mapper is _map_get_recent_airflow_failures


def test_get_airflow_dag_runs_carries_mapper() -> None:
    rt = get_airflow_dag_runs.__opensre_registered_tool__
    assert rt.evidence_mapper is _map_get_airflow_dag_runs


def test_get_airflow_task_instances_carries_mapper() -> None:
    rt = get_airflow_task_instances.__opensre_registered_tool__
    assert rt.evidence_mapper is _map_get_airflow_task_instances


class TestMapGetRecentAirflowFailures:
    def test_records_entry_with_dag_id(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_recent_airflow_failures(
            evidence,
            {
                "source": "airflow",
                "dag_id": "etl_daily",
                "failures": [{"state": "failed"}, {"state": "up_for_retry"}],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_recent_airflow_failures"
        assert entries[0]["summary"] == "2 failed/retrying task instance(s) for DAG 'etl_daily'"

    def test_records_nothing_when_no_failures(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_recent_airflow_failures(
            evidence, {"source": "airflow", "dag_id": "etl_daily", "failures": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_error_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_recent_airflow_failures(evidence, {"error": "dag_id is required"}, {})

        assert "catalog_entries" not in evidence


class TestMapGetAirflowDagRuns:
    def test_records_entry_with_failed_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_airflow_dag_runs(
            evidence,
            {
                "source": "airflow",
                "dag_id": "etl_daily",
                "dag_runs": [{"state": "success"}, {"state": "failed"}],
            },
            {"limit": 10},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_airflow_dag_runs"
        assert entries[0]["summary"] == "2 DAG run(s), 1 failed for 'etl_daily'"

    def test_records_zero_failed_as_a_genuine_finding(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_airflow_dag_runs(
            evidence,
            {"source": "airflow", "dag_id": "etl_daily", "dag_runs": [{"state": "success"}]},
            {"limit": 10},
        )

        assert evidence["catalog_entries"][0]["summary"] == "1 DAG run(s), 0 failed for 'etl_daily'"

    def test_cites_state_filter_instead_of_a_misleading_failed_count(self) -> None:
        """Regression: `state` filters the API query itself, so a
        state='success' query returning zero 'failed' runs does not mean
        there were no failures -- they were filtered out before the mapper
        ever saw them. Cite the filter, not a derived (and misleading)
        zero-failed count."""
        evidence: dict[str, Any] = {}

        _map_get_airflow_dag_runs(
            evidence,
            {
                "source": "airflow",
                "dag_id": "etl_daily",
                "dag_runs": [{"state": "success"}, {"state": "success"}],
            },
            {"limit": 10, "state": "success"},
        )

        summary = evidence["catalog_entries"][0]["summary"]
        assert summary == "2 DAG run(s) with state 'success' for 'etl_daily'"
        assert "failed" not in summary

    def test_qualifies_count_when_page_is_saturated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_airflow_dag_runs(
            evidence,
            {"source": "airflow", "dag_runs": [{"state": "success"}] * 10},
            {"limit": 10},
        )

        assert evidence["catalog_entries"][0]["summary"].startswith("10+ DAG run(s)")

    def test_records_nothing_when_no_dag_runs(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_airflow_dag_runs(evidence, {"source": "airflow", "dag_runs": []}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_error_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_airflow_dag_runs(evidence, {"error": "dag_id is required"}, {})

        assert "catalog_entries" not in evidence


class TestMapGetAirflowTaskInstances:
    def test_records_entry_with_failed_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_airflow_task_instances(
            evidence,
            {
                "source": "airflow",
                "dag_id": "etl_daily",
                "dag_run_id": "run-123",
                "task_instances": [{"state": "success"}, {"state": "failed"}],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_airflow_task_instances"
        assert entries[0]["summary"] == "2 task instance(s), 1 failed/retrying for run 'run-123'"

    def test_records_nothing_when_no_task_instances(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_airflow_task_instances(
            evidence, {"source": "airflow", "dag_run_id": "run-123", "task_instances": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_error_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_airflow_task_instances(evidence, {"error": "dag_run_id is required"}, {})

        assert "catalog_entries" not in evidence
