from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tests.synthetic_testing.schemas import (
    validate_alert,
    validate_answer_key,
    validate_cloudwatch_metrics,
    validate_performance_insights,
    validate_rds_events,
)

SUITE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class ScenarioAnswerKey:
    root_cause_category: str
    required_keywords: list[str]
    model_response: str


@dataclass(frozen=True)
class ScenarioFixture:
    scenario_id: str
    scenario_dir: Path
    alert: dict[str, Any]
    evidence: dict[str, Any]
    answer_key: ScenarioAnswerKey
    fault_script: str
    problem_md: str


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _parse_answer_yaml(path: Path) -> ScenarioAnswerKey:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected YAML object in {path}")
    validated = validate_answer_key(payload)
    return ScenarioAnswerKey(
        root_cause_category=validated["root_cause_category"].strip(),
        required_keywords=[k.strip() for k in validated["required_keywords"]],
        model_response=validated["model_response"].strip(),
    )


def _build_problem_md(alert: dict[str, Any], scenario_id: str) -> str:
    title = str(alert.get("title") or scenario_id)
    labels = alert.get("commonLabels", {}) or {}
    annotations = alert.get("commonAnnotations", {}) or {}

    parts = [
        f"# {title}",
        (
            "Service: RDS PostgreSQL"
            f" | Severity: {labels.get('severity', 'critical')}"
            f" | Scenario: {annotations.get('rds_failure_mode', scenario_id)}"
        ),
        f"Scenario ID: {scenario_id}",
    ]

    db_instance = annotations.get("db_instance_identifier") or annotations.get("db_instance")
    if db_instance:
        parts.append(f"DB instance: {db_instance}")

    db_cluster = annotations.get("db_cluster")
    if db_cluster:
        parts.append(f"DB cluster: {db_cluster}")

    summary = annotations.get("summary")
    if summary:
        parts.append(f"\nSummary: {summary}")

    error = annotations.get("error")
    if error and error != summary:
        parts.append(f"\nError: {error}")

    suspected = annotations.get("suspected_symptom")
    if suspected:
        parts.append(f"\nObserved symptom: {suspected}")

    return "\n".join(parts)


def _build_evidence(
    cloudwatch_metrics: dict[str, Any],
    rds_events: dict[str, Any],
    performance_insights: dict[str, Any],
) -> dict[str, Any]:
    return {
        "rds_metrics": cloudwatch_metrics,
        "rds_events": rds_events.get("events", []),
        "performance_insights": performance_insights,
    }


def load_scenario(scenario_dir: Path) -> ScenarioFixture:
    alert = validate_alert(_read_json(scenario_dir / "alert.json"))
    cloudwatch_metrics = validate_cloudwatch_metrics(_read_json(scenario_dir / "cloudwatch_metrics.json"))
    rds_events = validate_rds_events(_read_json(scenario_dir / "rds_events.json"))
    performance_insights = validate_performance_insights(_read_json(scenario_dir / "performance_insights.json"))
    answer_key = _parse_answer_yaml(scenario_dir / "answer.yml")
    fault_script = (scenario_dir / "fault_script.sh").read_text(encoding="utf-8")

    scenario_id = scenario_dir.name
    problem_md = _build_problem_md(alert, scenario_id)
    evidence = _build_evidence(cloudwatch_metrics, rds_events, performance_insights)

    return ScenarioFixture(
        scenario_id=scenario_id,
        scenario_dir=scenario_dir,
        alert=alert,
        evidence=evidence,
        answer_key=answer_key,
        fault_script=fault_script,
        problem_md=problem_md,
    )


def load_all_scenarios(root_dir: Path | None = None) -> list[ScenarioFixture]:
    base_dir = root_dir or SUITE_DIR
    scenario_dirs = sorted(path for path in base_dir.iterdir() if path.is_dir() and path.name[:3].isdigit())
    return [load_scenario(path) for path in scenario_dirs]
