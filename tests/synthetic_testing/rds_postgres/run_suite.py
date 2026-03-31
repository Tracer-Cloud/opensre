from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

from app.agent.nodes.plan_actions.detect_sources import detect_sources
from app.agent.nodes.root_cause_diagnosis.node import diagnose_root_cause
from app.agent.state import InvestigationState, make_initial_state
from tests.synthetic_testing.evidence_adapter import adapt_evidence_for_prompt
from tests.synthetic_testing.rds_postgres.scenario_loader import (
    SUITE_DIR,
    ScenarioFixture,
    load_all_scenarios,
)


@dataclass(frozen=True)
class ScenarioScore:
    scenario_id: str
    passed: bool
    root_cause_present: bool
    expected_category: str
    actual_category: str
    missing_keywords: list[str]
    matched_keywords: list[str]
    root_cause: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the synthetic RDS PostgreSQL RCA suite.")
    parser.add_argument(
        "--scenario",
        default="",
        help="Run a single scenario directory name, e.g. 001-replication-lag.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON results.",
    )
    return parser.parse_args(argv)


def prepare_scenario_state(fixture: ScenarioFixture) -> InvestigationState:
    alert = fixture.alert
    labels = alert.get("commonLabels", {}) or {}
    annotations = alert.get("commonAnnotations", {}) or {}

    alert_name = str(alert.get("title") or labels.get("alertname") or fixture.scenario_id)
    pipeline_name = str(labels.get("pipeline_name") or "rds-postgres-synthetic")
    severity = str(labels.get("severity") or "critical")

    state = make_initial_state(
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
        raw_alert=alert,
    )
    state["problem_md"] = fixture.problem_md
    state["alert_source"] = str(alert.get("alert_source") or "cloudwatch")
    state["evidence"] = adapt_evidence_for_prompt(fixture.evidence.as_dict())
    state["context"] = {
        "service": "rds",
        "engine": fixture.metadata.engine,
    }

    detected_sources = detect_sources(alert, state["context"], resolved_integrations=None)
    aws_metadata = dict(detected_sources.get("aws_metadata", {}))
    aws_metadata.setdefault("service", "rds")
    if annotations.get("db_instance_identifier"):
        aws_metadata.setdefault("resource_id", annotations["db_instance_identifier"])
    detected_sources["aws_metadata"] = aws_metadata
    state["available_sources"] = detected_sources
    return state


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def score_result(fixture: ScenarioFixture, diagnosis: dict[str, Any]) -> ScenarioScore:
    root_cause = str(diagnosis.get("root_cause") or "").strip()
    actual_category = str(diagnosis.get("root_cause_category") or "unknown").strip()
    root_cause_present = bool(root_cause and root_cause.lower() != "unable to determine root cause")

    evidence_text = " ".join(
        [
            root_cause,
            " ".join(claim.get("claim", "") for claim in diagnosis.get("validated_claims", [])),
            " ".join(claim.get("claim", "") for claim in diagnosis.get("non_validated_claims", [])),
            " ".join(diagnosis.get("causal_chain", [])),
        ]
    )
    normalized_output = _normalize_text(evidence_text)

    matched_keywords = [
        keyword
        for keyword in fixture.answer_key.required_keywords
        if _normalize_text(keyword) in normalized_output
    ]
    missing_keywords = [
        keyword for keyword in fixture.answer_key.required_keywords if keyword not in matched_keywords
    ]

    passed = (
        root_cause_present
        and actual_category == fixture.answer_key.root_cause_category
        and not missing_keywords
    )
    return ScenarioScore(
        scenario_id=fixture.scenario_id,
        passed=passed,
        root_cause_present=root_cause_present,
        expected_category=fixture.answer_key.root_cause_category,
        actual_category=actual_category,
        missing_keywords=missing_keywords,
        matched_keywords=matched_keywords,
        root_cause=root_cause,
    )


def run_scenario(fixture: ScenarioFixture) -> tuple[dict[str, Any], ScenarioScore]:
    state = prepare_scenario_state(fixture)
    diagnosis = diagnose_root_cause(state)
    state.update(diagnosis)
    return diagnosis, score_result(fixture, diagnosis)


def run_suite(argv: list[str] | None = None) -> list[ScenarioScore]:
    args = parse_args(argv)
    fixtures = load_all_scenarios(SUITE_DIR)
    if args.scenario:
        fixtures = [fixture for fixture in fixtures if fixture.scenario_id == args.scenario]
        if not fixtures:
            raise SystemExit(f"Unknown scenario: {args.scenario}")

    results: list[ScenarioScore] = []
    for fixture in fixtures:
        _, score = run_scenario(fixture)
        results.append(score)

    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        for result in results:
            status = "PASS" if result.passed else "FAIL"
            print(
                f"{status} {result.scenario_id} "
                f"category={result.actual_category} "
                f"missing_keywords={len(result.missing_keywords)}"
            )

        passed_count = sum(1 for result in results if result.passed)
        print(f"\nResults: {passed_count}/{len(results)} passed")

    return results


def main(argv: list[str] | None = None) -> int:
    results = run_suite(argv)
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
