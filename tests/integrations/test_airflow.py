from __future__ import annotations

import httpx
import pytest

from app.integrations.airflow import (
    AirflowConfig,
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
