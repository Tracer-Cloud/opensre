"""Tracer Airflow DAG/task investigation tools."""

from __future__ import annotations

import os
from typing import Any

from app.integrations.airflow import (
    AirflowConfig,
    build_airflow_config,
)
from app.integrations.airflow import (
    get_airflow_dag_runs as fetch_airflow_dag_runs,
)
from app.integrations.airflow import (
    get_airflow_task_instances as fetch_airflow_task_instances,
)
from app.integrations.airflow import (
    get_recent_airflow_failures as fetch_recent_airflow_failures,
)
from app.tools.tool_decorator import tool


def _airflow_available(sources: dict[str, Any]) -> bool:
    return "airflow" in sources


def _airflow_source(sources: dict[str, Any]) -> dict[str, Any]:
    source = sources.get("airflow", {})
    return source if isinstance(source, dict) else {}


def _airflow_config(sources: dict[str, Any]) -> AirflowConfig:
    source = _airflow_source(sources)
    return build_airflow_config(source)


def _airflow_dag_id(sources: dict[str, Any]) -> str:
    source = _airflow_source(sources)
    return str(
        source.get("dag_id") or source.get("pipeline_name") or os.getenv("AIRFLOW_DAG_ID", "")
    ).strip()


@tool(
    name="get_recent_airflow_failures",
    source="airflow",
    description="Fetch recent failed or retrying Airflow task evidence for a DAG.",
    use_cases=[
        "Investigating Airflow DAG failures",
        "Finding failed or retrying task instances",
        "Grounding RCA in Airflow DAG/task evidence",
    ],
    surfaces=("investigation", "chat"),
    requires=["dag_id"],
    input_schema={
        "type": "object",
        "properties": {
            "dag_id": {"type": "string"},
            "limit": {"type": "integer", "default": 5},
        },
        "required": ["dag_id"],
    },
    is_available=_airflow_available,
    extract_params=lambda sources: {
        "config": _airflow_config(sources),
        "dag_id": _airflow_dag_id(sources),
    },
)
def get_recent_airflow_failures(
    config: AirflowConfig,
    dag_id: str,
    limit: int = 5,
) -> dict[str, Any]:
    """Fetch recent failed or retrying Airflow task evidence for a DAG."""
    dag_id = dag_id or os.getenv("AIRFLOW_DAG_ID", "")
    if not dag_id:
        return {"error": "dag_id is required"}

    return {
        "source": "airflow",
        "dag_id": dag_id,
        "failures": fetch_recent_airflow_failures(
            config=config,
            dag_id=dag_id,
            limit=limit,
        ),
    }


@tool(
    name="get_airflow_dag_runs",
    source="airflow",
    description="Fetch recent Airflow DAG runs for a DAG.",
    use_cases=[
        "Checking recent Airflow DAG run state",
        "Finding failed DAG runs",
        "Validating Airflow orchestration state",
    ],
    surfaces=("investigation", "chat"),
    requires=["dag_id"],
    input_schema={
        "type": "object",
        "properties": {
            "dag_id": {"type": "string"},
            "limit": {"type": "integer", "default": 10},
            "state": {"type": "string"},
        },
        "required": ["dag_id"],
    },
    is_available=_airflow_available,
    extract_params=lambda sources: {
        "config": _airflow_config(sources),
        "dag_id": _airflow_dag_id(sources),
    },
)
def get_airflow_dag_runs(
    config: AirflowConfig,
    dag_id: str,
    limit: int = 10,
    state: str | None = None,
) -> dict[str, Any]:
    """Fetch recent Airflow DAG runs for a DAG."""
    dag_id = dag_id or os.getenv("AIRFLOW_DAG_ID", "")
    if not dag_id:
        return {"error": "dag_id is required"}

    return {
        "source": "airflow",
        "dag_id": dag_id,
        "dag_runs": fetch_airflow_dag_runs(
            config=config,
            dag_id=dag_id,
            limit=limit,
            state=state,
        ),
    }


@tool(
    name="get_airflow_task_instances",
    source="airflow",
    description="Fetch Airflow task instances for a specific DAG run.",
    use_cases=[
        "Inspecting failed Airflow task instances",
        "Finding task-level failure evidence",
        "Grounding RCA in Airflow task state",
    ],
    surfaces=("investigation", "chat"),
    requires=["dag_id", "dag_run_id"],
    input_schema={
        "type": "object",
        "properties": {
            "dag_id": {"type": "string"},
            "dag_run_id": {"type": "string"},
        },
        "required": ["dag_id", "dag_run_id"],
    },
    is_available=_airflow_available,
    extract_params=lambda sources: {
        "config": _airflow_config(sources),
        "dag_id": _airflow_dag_id(sources),
    },
)
def get_airflow_task_instances(
    config: AirflowConfig,
    dag_id: str,
    dag_run_id: str,
) -> dict[str, Any]:
    """Fetch Airflow task instances for a DAG run."""
    if not dag_id:
        return {"error": "dag_id is required"}
    if not dag_run_id:
        return {"error": "dag_run_id is required"}

    return {
        "source": "airflow",
        "dag_id": dag_id,
        "dag_run_id": dag_run_id,
        "task_instances": fetch_airflow_task_instances(
            config=config,
            dag_id=dag_id,
            dag_run_id=dag_run_id,
        ),
    }
