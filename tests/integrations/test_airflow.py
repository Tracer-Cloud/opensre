from __future__ import annotations

import httpx
import pytest

from app.integrations.airflow import (
    AirflowConfig,
    airflow_config_from_env,
    build_airflow_config,
    get_airflow_dag_runs,
    get_airflow_task_instances,
    get_recent_airflow_failures,
    validate_airflow_config,
)


def test_build_airflow_config_defaults() -> None:
    config = build_airflow_config({})

    assert config.base_url == "http://localhost:8080/api/v1"
    assert config.username == ""
    assert config.password == ""
    assert config.auth_token == ""
    assert config.timeout_seconds == 15.0
    assert config.verify_ssl is True
    assert config.max_results == 50


def test_validate_airflow_config_requires_auth() -> None:
    config = AirflowConfig()

    result = validate_airflow_config(config)

    assert result.ok is False
    assert "Airflow auth is required" in result.detail


def test_airflow_auth_omits_basic_auth_when_token_present() -> None:
    config = AirflowConfig(
        auth_token="test-token",
        username="airflow-user",
        password="super-secret",
    )

    assert config.headers["Authorization"] == "Bearer test-token"
    assert config.auth is None


def test_airflow_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIRFLOW_BASE_URL", "https://airflow.example.com/api/v1/")
    monkeypatch.setenv("AIRFLOW_AUTH_TOKEN", "env-token")
    monkeypatch.setenv("AIRFLOW_VERIFY_SSL", "false")
    monkeypatch.setenv("AIRFLOW_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("AIRFLOW_MAX_RESULTS", "25")

    config = airflow_config_from_env()

    assert config is not None
    assert config.base_url == "https://airflow.example.com/api/v1"
    assert config.auth_token == "env-token"
    assert config.verify_ssl is False
    assert config.timeout_seconds == 30.0
    assert config.max_results == 25


def test_get_airflow_dag_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    def _mock_request(
        method: str,
        url: str,
        headers=None,
        auth=None,
        params=None,
        json=None,
        timeout=None,
        verify=None,
    ) -> httpx.Response:
        assert method == "GET"
        assert "/dags/example_dag/dagRuns" in url
        return httpx.Response(
            200,
            json={
                "dag_runs": [
                    {
                        "dag_run_id": "manual__1",
                        "state": "failed",
                        "logical_date": "2026-04-01T00:00:00Z",
                    }
                ]
            },
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(httpx, "request", _mock_request)

    config = AirflowConfig(auth_token="test-token")
    runs = get_airflow_dag_runs(config=config, dag_id="example_dag")

    assert len(runs) == 1
    assert runs[0]["dag_run_id"] == "manual__1"


def test_get_airflow_task_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    def _mock_request(
        method: str,
        url: str,
        headers=None,
        auth=None,
        params=None,
        json=None,
        timeout=None,
        verify=None,
    ) -> httpx.Response:
        assert method == "GET"
        assert "/taskInstances" in url
        return httpx.Response(
            200,
            json={
                "task_instances": [
                    {
                        "task_id": "extract",
                        "state": "failed",
                        "try_number": 2,
                        "max_tries": 3,
                    }
                ]
            },
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(httpx, "request", _mock_request)

    config = AirflowConfig(auth_token="test-token")
    task_instances = get_airflow_task_instances(
        config=config,
        dag_id="example_dag",
        dag_run_id="manual__1",
    )

    assert len(task_instances) == 1
    assert task_instances[0]["task_id"] == "extract"
    assert task_instances[0]["state"] == "failed"


def test_get_recent_airflow_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def _mock_request(
        method: str,
        url: str,
        headers=None,
        auth=None,
        params=None,
        json=None,
        timeout=None,
        verify=None,
    ) -> httpx.Response:
        if "/dags/example_dag/dagRuns/manual__1/taskInstances" in url:
            return httpx.Response(
                200,
                json={
                    "task_instances": [
                        {
                            "task_id": "extract",
                            "state": "failed",
                            "try_number": 2,
                            "max_tries": 3,
                            "operator": "PythonOperator",
                        },
                        {
                            "task_id": "load",
                            "state": "success",
                            "try_number": 1,
                            "max_tries": 1,
                        },
                    ]
                },
                request=httpx.Request(method, url),
            )

        if "/dags/example_dag/dagRuns" in url:
            return httpx.Response(
                200,
                json={
                    "dag_runs": [
                        {
                            "dag_run_id": "manual__1",
                            "state": "failed",
                            "logical_date": "2026-04-01T00:00:00Z",
                            "run_type": "manual",
                        }
                    ]
                },
                request=httpx.Request(method, url),
            )

        return httpx.Response(
            404,
            json={"detail": "not found"},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(httpx, "request", _mock_request)

    config = AirflowConfig(auth_token="test-token")
    evidence = get_recent_airflow_failures(
        config=config,
        dag_id="example_dag",
    )

    assert len(evidence) == 1
    assert evidence[0]["source"] == "airflow"
    assert evidence[0]["dag_id"] == "example_dag"
    assert evidence[0]["dag_run_id"] == "manual__1"
    assert evidence[0]["task_id"] == "extract"
    assert evidence[0]["task_state"] == "failed"


def test_get_recent_airflow_failures_partial_run_error_preserves_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One run's task-instance fetch raises; evidence from the other run is preserved."""
    call_count = 0

    def _mock_request(
        method: str,
        url: str,
        headers=None,
        auth=None,
        params=None,
        json=None,
        timeout=None,
        verify=None,
    ) -> httpx.Response:
        nonlocal call_count

        # DAG runs endpoint — return two runs
        if "/dags/example_dag/dagRuns" in url and "taskInstances" not in url:
            return httpx.Response(
                200,
                json={
                    "dag_runs": [
                        {
                            "dag_run_id": "run_ok",
                            "state": "failed",
                            "logical_date": "2026-04-01T00:00:00Z",
                            "run_type": "manual",
                        },
                        {
                            "dag_run_id": "run_bad",
                            "state": "failed",
                            "logical_date": "2026-04-02T00:00:00Z",
                            "run_type": "manual",
                        },
                    ]
                },
                request=httpx.Request(method, url),
            )

        # run_ok: returns one failed task instance
        if "/dagRuns/run_ok/taskInstances" in url:
            return httpx.Response(
                200,
                json={
                    "task_instances": [
                        {
                            "task_id": "transform",
                            "state": "failed",
                            "try_number": 1,
                            "max_tries": 3,
                            "operator": "PythonOperator",
                        }
                    ]
                },
                request=httpx.Request(method, url),
            )

        # run_bad: simulates a 500 from the Airflow API
        if "/dagRuns/run_bad/taskInstances" in url:
            call_count += 1
            raise httpx.HTTPStatusError(
                "server error",
                request=httpx.Request(method, url),
                response=httpx.Response(500, request=httpx.Request(method, url)),
            )

        return httpx.Response(
            404,
            json={"detail": "not found"},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(httpx, "request", _mock_request)

    config = AirflowConfig(auth_token="test-token")
    evidence = get_recent_airflow_failures(config=config, dag_id="example_dag", limit=5)

    # Evidence from run_ok must be preserved despite run_bad raising
    assert len(evidence) == 1
    assert evidence[0]["dag_run_id"] == "run_ok"
    assert evidence[0]["task_id"] == "transform"
    assert evidence[0]["task_state"] == "failed"
    # Confirms the bad run was actually attempted (not silently skipped before the call)
    assert call_count == 1
