"""
Apache Airflow Integration E2E Tests.

Airflow is a supplementary investigation source, not necessarily the primary
alert source. These tests verify that the agent can validate connectivity,
read DAG runs and task instances, and use Airflow evidence during a realistic
investigation flow.

Required env vars:
    AIRFLOW_BASE_URL       - Airflow API base URL, e.g. http://localhost:8080/api/v1
    AIRFLOW_DAG_ID         - DAG ID to inspect

Auth env vars (one of the following is required):
    AIRFLOW_AUTH_TOKEN     - Bearer token for Airflow API
    AIRFLOW_USERNAME       - Basic auth username
    AIRFLOW_PASSWORD       - Basic auth password

Optional env vars:
    AIRFLOW_VERIFY_SSL      - true/false (defaults to true)
    AIRFLOW_TIMEOUT_SECONDS - request timeout (defaults to 15)
    AIRFLOW_TEST_DAG_RUN_ID - specific DAG run ID for targeted task-instance test
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

from app.integrations.airflow import (
    DEFAULT_AIRFLOW_BASE_URL,
    build_airflow_config,
    get_airflow_dag_runs,
    get_airflow_task_instances,
    get_recent_airflow_failures,
    validate_airflow_config,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _require_env() -> tuple[str, str, str, str, str]:
    """Return (base_url, dag_id, auth_token, username, password) or skip."""
    base_url = (
        os.getenv("AIRFLOW_BASE_URL", DEFAULT_AIRFLOW_BASE_URL).strip() or DEFAULT_AIRFLOW_BASE_URL
    )
    dag_id = os.getenv("AIRFLOW_DAG_ID", "").strip()
    auth_token = os.getenv("AIRFLOW_AUTH_TOKEN", "").strip()
    username = os.getenv("AIRFLOW_USERNAME", "").strip()
    password = os.getenv("AIRFLOW_PASSWORD", "").strip()

    missing = []
    if not dag_id:
        missing.append("AIRFLOW_DAG_ID")
    if not auth_token and not username:
        missing.append("AIRFLOW_AUTH_TOKEN or AIRFLOW_USERNAME/AIRFLOW_PASSWORD")

    if missing:
        pytest.skip(f"Airflow env vars not set: {', '.join(missing)}")

    return base_url, dag_id, auth_token, username, password


def _airflow_config(base_url: str, auth_token: str, username: str, password: str):
    return build_airflow_config(
        {
            "base_url": base_url,
            "auth_token": auth_token,
            "username": username,
            "password": password,
            "verify_ssl": os.getenv("AIRFLOW_VERIFY_SSL", "true").strip().lower()
            in ("true", "1", "yes"),
            "timeout_seconds": os.getenv("AIRFLOW_TIMEOUT_SECONDS", "15").strip(),
        }
    )


def test_airflow_connectivity():
    """Verify that the Airflow API is reachable and auth works."""
    base_url, dag_id, auth_token, username, password = _require_env()
    config = _airflow_config(base_url, auth_token, username, password)

    result = validate_airflow_config(config)

    assert result.ok, f"Airflow connectivity failed: {result.detail}"
    assert "Airflow connectivity successful" in result.detail


def test_airflow_list_dag_runs():
    """Fetch recent DAG runs for the configured DAG."""
    base_url, dag_id, auth_token, username, password = _require_env()
    config = _airflow_config(base_url, auth_token, username, password)

    dag_runs = get_airflow_dag_runs(
        config=config,
        dag_id=dag_id,
        limit=5,
    )

    assert isinstance(dag_runs, list), "Expected a list of DAG runs"
    if dag_runs:
        first = dag_runs[0]
        assert "dag_run_id" in first, f"Unexpected DAG run shape: {first.keys()}"
        assert "state" in first, f"DAG run missing state: {first.keys()}"


def test_airflow_list_task_instances():
    """Fetch task instances for a DAG run."""
    base_url, dag_id, auth_token, username, password = _require_env()
    config = _airflow_config(base_url, auth_token, username, password)

    dag_run_id = os.getenv("AIRFLOW_TEST_DAG_RUN_ID", "").strip()
    if not dag_run_id:
        dag_runs = get_airflow_dag_runs(
            config=config,
            dag_id=dag_id,
            limit=1,
        )
        if not dag_runs:
            pytest.skip(f"No DAG runs found for dag_id={dag_id}")
        dag_run_id = str(dag_runs[0].get("dag_run_id", "")).strip()

    if not dag_run_id:
        pytest.skip("No DAG run id available for task-instance test")

    task_instances = get_airflow_task_instances(
        config=config,
        dag_id=dag_id,
        dag_run_id=dag_run_id,
    )

    assert isinstance(task_instances, list), "Expected a list of task instances"
    if task_instances:
        first = task_instances[0]
        assert "task_id" in first, f"Unexpected task instance shape: {first.keys()}"
        assert "state" in first, f"Task instance missing state: {first.keys()}"


def test_airflow_recent_failures():
    """Fetch recent failed or retrying Airflow task evidence."""
    base_url, dag_id, auth_token, username, password = _require_env()
    config = _airflow_config(base_url, auth_token, username, password)

    evidence = get_recent_airflow_failures(
        config=config,
        dag_id=dag_id,
        limit=5,
    )

    assert isinstance(evidence, list), "Expected a list of Airflow evidence"
    if evidence:
        first = evidence[0]
        assert first.get("source") == "airflow"
        assert first.get("dag_id") == dag_id
        assert "task_id" in first
        assert "task_state" in first


def test_airflow_investigation_e2e():
    """
    Full investigation flow with Airflow as a supplementary evidence source.

    Simulates a production alert while Airflow is resolved from env/config.
    The agent is expected to use Airflow tools alongside the alert context to
    produce a root cause.
    """
    base_url, dag_id, auth_token, username, password = _require_env()

    from app.cli.investigate import run_investigation_cli

    fixture_path = FIXTURES_DIR / "airflow_task_failure_alert.json"
    raw_alert = json.loads(fixture_path.read_text())

    investigation_result = run_investigation_cli(
        alert_name=f"Airflow DAG failure: {dag_id}",
        pipeline_name=dag_id,
        severity="critical",
        raw_alert=raw_alert,
    )

    root_cause = investigation_result.get("root_cause", "")

    assert root_cause, (
        "Investigation produced no root cause. "
        "Check Airflow credentials, DAG visibility, and LLM API key configuration."
    )
