from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from tests.synthetic.schemas import (
    ScenarioEvidence,
    ScenarioMetadataSchema,
    validate_alert,
    validate_answer_key,
    validate_cloudwatch_metrics,
    validate_performance_insights,
    validate_rds_events,
    validate_scenario_metadata,
)

SUITE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class ScenarioMetadata:
    schema_version: str
    scenario_id: str
    engine: str
    engine_version: str
    instance_class: str
    region: str
    db_instance_identifier: str
    db_cluster: str
    failure_mode: str
    severity: str
    available_evidence: list[str]
    scenario_difficulty: int = 1
    adversarial_signals: list[str] = ()  # type: ignore[assignment]
    depends_on: str = ""


@dataclass(frozen=True)
class ScenarioAnswerKey:
    root_cause_category: str
    required_keywords: list[str]
    model_response: str
    forbidden_categories: list[str] = ()  # type: ignore[assignment]
    forbidden_keywords: list[str] = ()  # type: ignore[assignment]
    required_evidence_sources: list[str] = ()  # type: ignore[assignment]
    optimal_trajectory: list[str] = ()  # type: ignore[assignment]
    max_investigation_loops: int = 1
    ruling_out_keywords: list[str] = ()  # type: ignore[assignment]
    required_queries: list[str] = ()     # type: ignore[assignment]


@dataclass(frozen=True)
class ScenarioFixture:
    scenario_id: str
    scenario_dir: Path
    alert: dict[str, Any]
    evidence: ScenarioEvidence
    metadata: ScenarioMetadata
    answer_key: ScenarioAnswerKey
    problem_md: str


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _read_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected YAML object in {path}")
    return payload


def _parse_scenario_yaml(path: Path) -> ScenarioMetadata:
    raw = _read_yaml(path)
    validated: ScenarioMetadataSchema = validate_scenario_metadata(raw)
    return ScenarioMetadata(
        schema_version=validated["schema_version"],
        scenario_id=validated["scenario_id"],
        engine=validated["engine"],
        engine_version=validated["engine_version"],
        instance_class=validated["instance_class"],
        region=validated["region"],
        db_instance_identifier=validated["db_instance_identifier"],
        db_cluster=validated.get("db_cluster", ""),
        failure_mode=validated["failure_mode"],
        severity=validated["severity"],
        available_evidence=list(validated["available_evidence"]),
        scenario_difficulty=validated.get("scenario_difficulty", 1),  # type: ignore[arg-type]
        adversarial_signals=list(validated.get("adversarial_signals") or []),
        depends_on=validated.get("depends_on", ""),  # type: ignore[arg-type]
    )


def _parse_answer_yaml(path: Path) -> ScenarioAnswerKey:
    payload = _read_yaml(path)
    validated = validate_answer_key(payload)
    return ScenarioAnswerKey(
        root_cause_category=validated["root_cause_category"].strip(),
        required_keywords=[k.strip() for k in validated["required_keywords"]],
        model_response=validated["model_response"].strip(),
        forbidden_categories=list(validated.get("forbidden_categories") or []),
        forbidden_keywords=list(validated.get("forbidden_keywords") or []),
        required_evidence_sources=list(validated.get("required_evidence_sources") or []),
        optimal_trajectory=list(validated.get("optimal_trajectory") or []),
        max_investigation_loops=int(validated.get("max_investigation_loops") or 1),
        ruling_out_keywords=list(validated.get("ruling_out_keywords") or []),
        required_queries=list(validated.get("required_queries") or []),
    )


def _build_problem_md(alert: dict[str, Any], metadata: ScenarioMetadata) -> str:
    title = str(alert.get("title") or metadata.scenario_id)
    annotations = alert.get("commonAnnotations", {}) or {}

    parts = [
        f"# {title}",
        (
            f"Service: RDS {metadata.engine.upper()}"
            f" | Severity: {metadata.severity}"
            f" | Scenario: {metadata.failure_mode}"
        ),
        f"Scenario ID: {metadata.scenario_id}",
        f"DB instance: {metadata.db_instance_identifier}",
    ]

    if metadata.db_cluster:
        parts.append(f"DB cluster: {metadata.db_cluster}")

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
    scenario_dir: Path,
    available_evidence: list[str],
) -> ScenarioEvidence:
    """Load only the evidence sources declared in scenario.yml:available_evidence."""
    rds_metrics = None
    rds_events = None
    performance_insights = None

    if "rds_metrics" in available_evidence:
        rds_metrics = validate_cloudwatch_metrics(_read_json(scenario_dir / "cloudwatch_metrics.json"))

    if "rds_events" in available_evidence:
        raw_events = validate_rds_events(_read_json(scenario_dir / "rds_events.json"))
        rds_events = raw_events.get("events", [])

    if "performance_insights" in available_evidence:
        performance_insights = validate_performance_insights(
            _read_json(scenario_dir / "performance_insights.json")
        )

    return ScenarioEvidence(
        rds_metrics=rds_metrics,
        rds_events=rds_events,
        performance_insights=performance_insights,
    )


def load_scenario(scenario_dir: Path) -> ScenarioFixture:
    metadata = _parse_scenario_yaml(scenario_dir / "scenario.yml")
    alert = cast(dict[str, Any], validate_alert(_read_json(scenario_dir / "alert.json")))
    evidence = _build_evidence(scenario_dir, metadata.available_evidence)
    answer_key = _parse_answer_yaml(scenario_dir / "answer.yml")
    problem_md = _build_problem_md(alert, metadata)

    return ScenarioFixture(
        scenario_id=scenario_dir.name,
        scenario_dir=scenario_dir,
        alert=alert,
        evidence=evidence,
        metadata=metadata,
        answer_key=answer_key,
        problem_md=problem_md,
    )


def load_all_scenarios(root_dir: Path | None = None) -> list[ScenarioFixture]:
    base_dir = root_dir or SUITE_DIR
    scenario_dirs = sorted(
        path for path in base_dir.iterdir() if path.is_dir() and path.name[:3].isdigit()
    )
    return [load_scenario(path) for path in scenario_dirs]
