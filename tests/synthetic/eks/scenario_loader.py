from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

from tests.synthetic.k8s_schemas import (
    K8sScenarioEvidence,
    K8sScenarioMetadataSchema,
    validate_datadog_logs,
    validate_datadog_monitors,
    validate_eks_deployments,
    validate_eks_events,
    validate_eks_node_health,
    validate_eks_pod_logs,
    validate_eks_pods,
    validate_k8s_alert,
    validate_k8s_answer_key,
    validate_k8s_scenario_metadata,
)

SUITE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class K8sScenarioMetadata:
    schema_version: str
    scenario_id: str
    engine: str
    cluster_name: str
    namespace: str
    workload_type: str
    workload_name: str
    region: str
    failure_mode: str
    severity: str
    available_evidence: list[str]
    scenario_difficulty: int = 1
    adversarial_signals: list[str] = field(default_factory=list)
    depends_on: str = ""


@dataclass(frozen=True)
class K8sScenarioAnswerKey:
    root_cause_category: str
    required_keywords: list[str]
    model_response: str
    forbidden_categories: list[str] = field(default_factory=list)
    forbidden_keywords: list[str] = field(default_factory=list)
    required_evidence_sources: list[str] = field(default_factory=list)
    optimal_trajectory: list[str] = field(default_factory=list)
    max_investigation_loops: int = 1
    ruling_out_keywords: list[str] = field(default_factory=list)
    required_queries: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class K8sScenarioFixture:
    scenario_id: str
    scenario_dir: Path
    alert: dict[str, Any]
    evidence: K8sScenarioEvidence
    metadata: K8sScenarioMetadata
    answer_key: K8sScenarioAnswerKey
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


# ---------------------------------------------------------------------------
# Base-inheritance helpers
# ---------------------------------------------------------------------------


def _resolve_base_dir(suite_dir: Path, base_id: str) -> Path:
    """Find the base scenario directory by its directory name (e.g. '000-healthy')."""
    base_dir = suite_dir / base_id
    if not base_dir.is_dir():
        raise ValueError(f"Base scenario '{base_id}' not found at {base_dir}")
    base_raw = _read_yaml(base_dir / "scenario.yml")
    if "base" in base_raw:
        raise ValueError(
            f"Chained inheritance is not supported: base scenario '{base_id}' "
            f"itself declares base '{base_raw['base']}'"
        )
    return base_dir


def _merge_scenario_yaml(
    base_raw: dict[str, Any], scenario_raw: dict[str, Any]
) -> dict[str, Any]:
    """Shallow-merge scenario overrides on top of base metadata.

    scenario_raw values win. The ``base`` directive is consumed and removed.
    """
    merged = {**base_raw, **{k: v for k, v in scenario_raw.items() if k != "base"}}
    merged.pop("base", None)
    return merged


def _resolve_evidence_path(
    scenario_dir: Path, base_dir: Path | None, filename: str
) -> Path:
    """Return the scenario's own evidence file if it exists, otherwise the base's."""
    for search_dir in (scenario_dir, base_dir):
        if search_dir is None:
            continue
        candidate = search_dir / filename
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Evidence '{filename}' not found in {scenario_dir}"
        + (f" or base {base_dir}" if base_dir else "")
    )


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _validated_metadata(raw: dict[str, Any]) -> K8sScenarioMetadata:
    """Validate a (possibly merged) raw dict and return a K8sScenarioMetadata."""
    validated: K8sScenarioMetadataSchema = validate_k8s_scenario_metadata(raw)
    return K8sScenarioMetadata(
        schema_version=validated["schema_version"],
        scenario_id=validated["scenario_id"],
        engine=validated["engine"],
        cluster_name=validated["cluster_name"],
        namespace=validated["namespace"],
        workload_type=validated["workload_type"],
        workload_name=validated["workload_name"],
        region=validated["region"],
        failure_mode=validated["failure_mode"],
        severity=validated["severity"],
        available_evidence=list(validated["available_evidence"]),
        scenario_difficulty=validated.get("scenario_difficulty", 1),  # type: ignore[arg-type]
        adversarial_signals=list(validated.get("adversarial_signals") or []),
        depends_on=validated.get("depends_on", ""),  # type: ignore[arg-type]
    )


def _parse_scenario_yaml(path: Path) -> tuple[K8sScenarioMetadata, Path | None]:
    """Parse scenario.yml, resolving base inheritance if declared.

    Returns (metadata, base_dir) where base_dir is the resolved base scenario
    directory, or None if no ``base`` field was declared.
    """
    raw = _read_yaml(path)
    base_id = raw.get("base")
    base_dir: Path | None = None

    if base_id:
        suite_dir = path.parent.parent
        base_dir = _resolve_base_dir(suite_dir, base_id)
        base_raw = _read_yaml(base_dir / "scenario.yml")
        raw = _merge_scenario_yaml(base_raw, raw)

    return _validated_metadata(raw), base_dir


def _parse_answer_yaml(path: Path) -> K8sScenarioAnswerKey:
    payload = _read_yaml(path)
    validated = validate_k8s_answer_key(payload)
    return K8sScenarioAnswerKey(
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


def _build_problem_md(alert: dict[str, Any], metadata: K8sScenarioMetadata) -> str:
    title = str(alert.get("title") or metadata.scenario_id)
    annotations = alert.get("commonAnnotations", {}) or {}

    parts = [
        f"# {title}",
        (
            f"Platform: {metadata.engine.upper()}"
            f" | Severity: {metadata.severity}"
            f" | Scenario: {metadata.failure_mode}"
        ),
        f"Scenario ID: {metadata.scenario_id}",
        f"Cluster: {metadata.cluster_name}",
        f"Namespace: {metadata.namespace}",
        f"Workload: {metadata.workload_type}/{metadata.workload_name}",
    ]

    summary = annotations.get("summary")
    if summary:
        parts.append(f"\nSummary: {summary}")

    description = annotations.get("description")
    if description and description != summary:
        parts.append(f"\nDescription: {description}")

    suspected = annotations.get("suspected_symptom")
    if suspected:
        parts.append(f"\nObserved symptom: {suspected}")

    return "\n".join(parts)


def _build_evidence(
    scenario_dir: Path,
    available_evidence: list[str],
    base_dir: Path | None = None,
) -> K8sScenarioEvidence:
    """Load only the evidence sources declared in scenario.yml:available_evidence.

    When *base_dir* is set, evidence files missing from *scenario_dir* are
    resolved from the base scenario directory (file-level fallback).
    """
    eks_pods = None
    eks_events = None
    eks_deployments = None
    eks_node_health = None
    eks_pod_logs = None
    datadog_logs = None
    datadog_monitors = None

    if "eks_pods" in available_evidence:
        path = _resolve_evidence_path(scenario_dir, base_dir, "eks_pods.json")
        eks_pods = validate_eks_pods(_read_json(path))

    if "eks_events" in available_evidence:
        path = _resolve_evidence_path(scenario_dir, base_dir, "eks_events.json")
        eks_events = validate_eks_events(_read_json(path))

    if "eks_deployments" in available_evidence:
        path = _resolve_evidence_path(scenario_dir, base_dir, "eks_deployments.json")
        eks_deployments = validate_eks_deployments(_read_json(path))

    if "eks_node_health" in available_evidence:
        path = _resolve_evidence_path(scenario_dir, base_dir, "eks_node_health.json")
        eks_node_health = validate_eks_node_health(_read_json(path))

    if "eks_pod_logs" in available_evidence:
        path = _resolve_evidence_path(scenario_dir, base_dir, "eks_pod_logs.json")
        eks_pod_logs = validate_eks_pod_logs(_read_json(path))

    if "datadog_logs" in available_evidence:
        path = _resolve_evidence_path(scenario_dir, base_dir, "datadog_logs.json")
        datadog_logs = validate_datadog_logs(_read_json(path))

    if "datadog_monitors" in available_evidence:
        path = _resolve_evidence_path(scenario_dir, base_dir, "datadog_monitors.json")
        datadog_monitors = validate_datadog_monitors(_read_json(path))

    return K8sScenarioEvidence(
        eks_pods=eks_pods,
        eks_events=eks_events,
        eks_deployments=eks_deployments,
        eks_node_health=eks_node_health,
        eks_pod_logs=eks_pod_logs,
        datadog_logs=datadog_logs,
        datadog_monitors=datadog_monitors,
    )


def load_scenario(scenario_dir: Path) -> K8sScenarioFixture:
    metadata, base_dir = _parse_scenario_yaml(scenario_dir / "scenario.yml")

    alert_path = _resolve_evidence_path(scenario_dir, base_dir, "alert.json")
    alert = cast(dict[str, Any], validate_k8s_alert(_read_json(alert_path)))

    evidence = _build_evidence(scenario_dir, metadata.available_evidence, base_dir)
    answer_key = _parse_answer_yaml(scenario_dir / "answer.yml")
    problem_md = _build_problem_md(alert, metadata)

    return K8sScenarioFixture(
        scenario_id=scenario_dir.name,
        scenario_dir=scenario_dir,
        alert=alert,
        evidence=evidence,
        metadata=metadata,
        answer_key=answer_key,
        problem_md=problem_md,
    )


def load_all_scenarios(root_dir: Path | None = None) -> list[K8sScenarioFixture]:
    base_dir = root_dir or SUITE_DIR
    scenario_dirs = sorted(
        path for path in base_dir.iterdir() if path.is_dir() and path.name[:3].isdigit()
    )
    return [load_scenario(path) for path in scenario_dirs]
