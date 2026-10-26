"""Evidence mappers for the Tracer Airflow DAG/task investigation tools."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from infrastructure.text.truncation import truncate

#: These tools have no ``available``/success flag -- ``{"error": ...}`` is the
#: only failure signal, and a request failure (auth, network, 5xx) inside the
#: config-layer fetch functions is swallowed into an empty list rather than
#: surfaced. A mapper therefore cannot distinguish "genuinely zero results"
#: from "the fetch silently failed", so it stays silent on empty lists rather
#: than assert a false zero -- the same conservative choice made throughout
#: this codebase's other evidence mappers.
_ID_SUMMARY_TRUNCATE_LEN = 60
_FAILED_DAG_RUN_STATES = frozenset({"failed"})
_FAILED_TASK_STATES = frozenset({"failed", "upstream_failed", "up_for_retry"})


def _safe_id(value: str) -> str:
    return truncate(str(value).replace("\n", " "), _ID_SUMMARY_TRUNCATE_LEN)


def map_get_recent_airflow_failures(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the count of failed/retrying task instances found for the DAG."""
    if output.get("error"):
        return
    failures = output.get("failures") or []
    if not failures:
        return
    summary = f"{len(failures)} failed/retrying task instance(s)"
    dag_id = output.get("dag_id")
    if dag_id:
        summary += f" for DAG '{_safe_id(dag_id)}'"
    record_evidence_entry(
        evidence,
        source="get_recent_airflow_failures",
        label="Airflow Recent Failures",
        summary=summary,
    )


def map_get_airflow_dag_runs(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the DAG run count and how many are in a failed state.

    ``state`` filters the API query itself (only runs in that state come
    back), so when it's set the "N failed" count would either be trivially
    all of them (state="failed") or a misleading, unqualified zero for any
    other state -- the caller filtered failures out of view, not confirmed
    their absence. Cite the filter instead of a derived failure count in
    that case.
    """
    if output.get("error"):
        return
    dag_runs = output.get("dag_runs") or []
    if not dag_runs:
        return
    total = len(dag_runs)
    requested_limit = tool_input.get("limit", 10)
    count_label = f"{total}+" if total >= max(requested_limit, 1) else str(total)
    state_filter = tool_input.get("state")
    if state_filter:
        summary = f"{count_label} DAG run(s) with state '{_safe_id(str(state_filter))}'"
    else:
        failed = sum(
            1 for run in dag_runs if str(run.get("state", "")).lower() in _FAILED_DAG_RUN_STATES
        )
        summary = f"{count_label} DAG run(s), {failed} failed"
    dag_id = output.get("dag_id")
    if dag_id:
        summary += f" for '{_safe_id(dag_id)}'"
    record_evidence_entry(
        evidence,
        source="get_airflow_dag_runs",
        label="Airflow DAG Runs",
        summary=summary,
    )


def map_get_airflow_task_instances(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the task instance count and how many failed or are up for retry."""
    if output.get("error"):
        return
    task_instances = output.get("task_instances") or []
    if not task_instances:
        return
    failed = sum(
        1 for t in task_instances if str(t.get("state", "")).lower() in _FAILED_TASK_STATES
    )
    summary = f"{len(task_instances)} task instance(s), {failed} failed/retrying"
    dag_run_id = output.get("dag_run_id")
    if dag_run_id:
        summary += f" for run '{_safe_id(dag_run_id)}'"
    record_evidence_entry(
        evidence,
        source="get_airflow_task_instances",
        label="Airflow Task Instances",
        summary=summary,
    )
