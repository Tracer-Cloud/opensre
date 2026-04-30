"""Shared Apache Airflow integration helpers."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import Field, field_validator

from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

DEFAULT_AIRFLOW_BASE_URL = "http://localhost:8080/api/v1"
DEFAULT_AIRFLOW_TIMEOUT_SECONDS = 15.0
DEFAULT_AIRFLOW_MAX_RESULTS = 50


class AirflowConfig(StrictConfigModel):
    """Normalized Airflow connection settings."""

    base_url: str = DEFAULT_AIRFLOW_BASE_URL
    username: str = ""
    password: str = ""
    auth_token: str = ""
    timeout_seconds: float = Field(default=DEFAULT_AIRFLOW_TIMEOUT_SECONDS, gt=0)
    verify_ssl: bool = True
    max_results: int = Field(default=DEFAULT_AIRFLOW_MAX_RESULTS, gt=0, le=200)

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_AIRFLOW_BASE_URL).strip().rstrip("/")
        return normalized or DEFAULT_AIRFLOW_BASE_URL

    @field_validator("username", "password", "auth_token", mode="before")
    @classmethod
    def _normalize_str(cls, value: Any) -> str:
        return str(value or "").strip()

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    @property
    def auth(self) -> tuple[str, str] | None:
        if self.username and not self.auth_token:
            return (self.username, self.password)
        return None

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and (self.auth_token or self.username))


@dataclass(frozen=True)
class AirflowValidationResult:
    """Result of validating an Airflow integration."""

    ok: bool
    detail: str


def build_airflow_config(raw: dict[str, Any] | None) -> AirflowConfig:
    """Build a normalized Airflow config object from env/store data."""
    return AirflowConfig.model_validate(raw or {})


def airflow_config_from_env() -> AirflowConfig | None:
    """Load an Airflow config from env vars."""
    username = os.getenv("AIRFLOW_USERNAME", "").strip()
    auth_token = os.getenv("AIRFLOW_AUTH_TOKEN", "").strip()

    if not username and not auth_token:
        return None

    return build_airflow_config(
        {
            "base_url": os.getenv("AIRFLOW_BASE_URL", DEFAULT_AIRFLOW_BASE_URL).strip()
            or DEFAULT_AIRFLOW_BASE_URL,
            "username": username,
            "password": os.getenv("AIRFLOW_PASSWORD", "").strip(),
            "auth_token": auth_token,
            "timeout_seconds": os.getenv(
                "AIRFLOW_TIMEOUT_SECONDS",
                str(DEFAULT_AIRFLOW_TIMEOUT_SECONDS),
            ),
            "verify_ssl": os.getenv("AIRFLOW_VERIFY_SSL", "true").strip().lower()
            in ("true", "1", "yes"),
            "max_results": os.getenv(
                "AIRFLOW_MAX_RESULTS", str(DEFAULT_AIRFLOW_MAX_RESULTS)
            ).strip(),
        }
    )


def _request_json(
    config: AirflowConfig,
    method: str,
    path: str,
    *,
    params: list[tuple[str, str | int | float | bool | None]] | None = None,
    json: dict[str, Any] | None = None,
) -> Any:
    """Make an Airflow API request and return parsed JSON."""
    url = f"{config.base_url}{path}"
    response = httpx.request(
        method,
        url,
        headers=config.headers,
        auth=config.auth,
        params=params,
        json=json,
        timeout=config.timeout_seconds,
        verify=config.verify_ssl,
    )
    response.raise_for_status()
    return response.json()


def validate_airflow_config(config: AirflowConfig) -> AirflowValidationResult:
    """Validate Airflow connectivity with a lightweight DAG query."""
    if not config.is_configured:
        return AirflowValidationResult(
            ok=False,
            detail="Airflow auth is required. Provide AIRFLOW_AUTH_TOKEN or AIRFLOW_USERNAME/AIRFLOW_PASSWORD.",
        )

    try:
        payload = validate_airflow_connection(config=config)
        dags = payload.get("dags", []) if isinstance(payload, dict) else []
        total_entries = (
            payload.get("total_entries", len(dags)) if isinstance(payload, dict) else len(dags)
        )
        return AirflowValidationResult(
            ok=True,
            detail=f"Airflow connectivity successful. Reachable DAG API; total visible DAGs: {total_entries}.",
        )
    except httpx.HTTPStatusError as err:
        detail = err.response.text.strip() or str(err)
        return AirflowValidationResult(ok=False, detail=f"Airflow validation failed: {detail}")
    except Exception as err:  # noqa: BLE001
        return AirflowValidationResult(ok=False, detail=f"Airflow validation failed: {err}")


def validate_airflow_connection(
    *,
    config: AirflowConfig,
) -> dict[str, Any]:
    """Validate Airflow connection."""
    payload = _request_json(
        config,
        "GET",
        "/dags",
        params=[("limit", 1)],
    )
    return payload if isinstance(payload, dict) else {}


def get_airflow_dag_runs(
    *,
    config: AirflowConfig,
    dag_id: str,
    limit: int = 10,
    state: str | None = None,
    order_by: str = "-start_date",
) -> list[dict[str, Any]]:
    """Fetch DAG runs for a given DAG."""
    effective_limit = min(limit, config.max_results)
    encoded_dag_id = quote(dag_id, safe="")

    params: list[tuple[str, str | int | float | bool | None]] = [
        ("limit", effective_limit),
        ("order_by", order_by),
    ]
    if state:
        params.append(("state", state))

    payload = _request_json(
        config,
        "GET",
        f"/dags/{encoded_dag_id}/dagRuns",
        params=params,
    )
    if not isinstance(payload, dict):
        return []
    dag_runs = payload.get("dag_runs", [])
    return dag_runs if isinstance(dag_runs, list) else []


def get_airflow_task_instances(
    *,
    config: AirflowConfig,
    dag_id: str,
    dag_run_id: str,
) -> list[dict[str, Any]]:
    """Fetch task instances for a given DAG run."""
    encoded_dag_id = quote(dag_id, safe="")
    encoded_dag_run_id = quote(dag_run_id, safe="")

    payload = _request_json(
        config,
        "GET",
        f"/dags/{encoded_dag_id}/dagRuns/{encoded_dag_run_id}/taskInstances",
    )
    if not isinstance(payload, dict):
        return []
    task_instances = payload.get("task_instances", [])
    return task_instances if isinstance(task_instances, list) else []


def _to_failure_evidence(
    *,
    dag_id: str,
    dag_run: dict[str, Any],
    task_instance: dict[str, Any],
) -> dict[str, Any]:
    """Normalize a failed or retrying task instance into investigation-friendly evidence."""
    start_date = task_instance.get("start_date") or dag_run.get("start_date")
    end_date = task_instance.get("end_date") or dag_run.get("end_date")
    state = task_instance.get("state", "")
    try_number = task_instance.get("try_number")
    max_tries = task_instance.get("max_tries")
    duration = task_instance.get("duration")

    return {
        "source": "airflow",
        "dag_id": dag_id,
        "dag_run_id": dag_run.get("dag_run_id", ""),
        "logical_date": dag_run.get("logical_date", ""),
        "run_type": dag_run.get("run_type", ""),
        "dag_run_state": dag_run.get("state", ""),
        "task_id": task_instance.get("task_id", ""),
        "task_state": state,
        "operator": task_instance.get("operator", ""),
        "try_number": try_number,
        "max_tries": max_tries,
        "queued_dttm": task_instance.get("queued_dttm", ""),
        "start_date": start_date,
        "end_date": end_date,
        "duration": duration,
        "hostname": task_instance.get("hostname", ""),
        "unixname": task_instance.get("unixname", ""),
        "pool": task_instance.get("pool", ""),
        "queue": task_instance.get("queue", ""),
        "priority_weight": task_instance.get("priority_weight"),
    }


def get_recent_airflow_failures(
    *,
    config: AirflowConfig,
    dag_id: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Fetch recent failed or retrying task evidence for a DAG.

    Strategy:
    - fetch recent DAG runs
    - fetch task instances for each run
    - return failed/up_for_retry/upstream_failed task evidence
    """
    dag_runs = get_airflow_dag_runs(
        config=config,
        dag_id=dag_id,
        limit=limit,
    )

    evidence: list[dict[str, Any]] = []
    interesting_states = {"failed", "up_for_retry", "upstream_failed"}

    for dag_run in dag_runs:
        dag_run_id = str(dag_run.get("dag_run_id", "")).strip()
        if not dag_run_id:
            continue

        try:
            task_instances = get_airflow_task_instances(
                config=config,
                dag_id=dag_id,
                dag_run_id=dag_run_id,
            )
        except Exception as err:
            logger.warning(
                "Failed to fetch Airflow task instances for dag_run_id=%s: %s",
                dag_run_id,
                type(err).__name__,
            )
            continue

        for task_instance in task_instances:
            state = str(task_instance.get("state", "")).strip().lower()
            if state not in interesting_states:
                continue
            evidence.append(
                _to_failure_evidence(
                    dag_id=dag_id,
                    dag_run=dag_run,
                    task_instance=task_instance,
                )
            )

    return evidence
