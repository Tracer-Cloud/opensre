from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

from app.agent.runners import run_investigation
from tests.synthetic.mock_grafana.backend import FixtureGrafanaBackend
from tests.synthetic.rds_postgres.scenario_loader import (
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
    failure_reason: str = ""


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
    parser.add_argument(
        "--mock-grafana",
        action="store_true",
        dest="mock_grafana",
        help="Serve fixture data via FixtureGrafanaBackend instead of real Grafana calls.",
    )
    return parser.parse_args(argv)


def _build_resolved_integrations(
    fixture: ScenarioFixture,
    use_mock_grafana: bool,
) -> dict[str, Any] | None:
    """Build pre-resolved integrations to inject into run_investigation.

    When use_mock_grafana is True, injects a FixtureGrafanaBackend so the
    full agentic pipeline (plan → investigate → diagnose) uses fixture data
    instead of making real Grafana API calls.
    """
    if not use_mock_grafana:
        return None
    return {
        "grafana": {
            "endpoint": "",
            "api_key": "",
            "_backend": FixtureGrafanaBackend(fixture),
        }
    }


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def score_result(fixture: ScenarioFixture, final_state: dict[str, Any]) -> ScenarioScore:
    root_cause = str(final_state.get("root_cause") or "").strip()
    actual_category = str(final_state.get("root_cause_category") or "unknown").strip()
    root_cause_present = bool(root_cause and root_cause.lower() != "unable to determine root cause")

    evidence_text = " ".join(
        [
            root_cause,
            " ".join(
                claim.get("claim", "") for claim in final_state.get("validated_claims", [])
            ),
            " ".join(
                claim.get("claim", "") for claim in final_state.get("non_validated_claims", [])
            ),
            " ".join(final_state.get("causal_chain", [])),
        ]
    )
    normalized_output = _normalize_text(evidence_text)

    matched_keywords = [
        keyword
        for keyword in fixture.answer_key.required_keywords
        if _normalize_text(keyword) in normalized_output
    ]
    missing_keywords = [
        keyword
        for keyword in fixture.answer_key.required_keywords
        if keyword not in matched_keywords
    ]

    answer_key = fixture.answer_key
    failure_reason = ""

    # 1. Category match
    if not root_cause_present:
        failure_reason = "no root cause in output"
    elif actual_category != answer_key.root_cause_category:
        failure_reason = f"wrong category: got {actual_category!r}, expected {answer_key.root_cause_category!r}"
    elif missing_keywords:
        failure_reason = f"missing required keywords: {missing_keywords}"
    # 2. Forbidden category check (level 2+ adversarial)
    elif answer_key.forbidden_categories and actual_category in answer_key.forbidden_categories:
        failure_reason = f"forbidden category in output: {actual_category!r}"
    # 3. Forbidden keyword check — none of these may appear in evidence_text
    elif answer_key.forbidden_keywords:
        forbidden_hits = [
            kw for kw in answer_key.forbidden_keywords
            if _normalize_text(kw) in normalized_output
        ]
        if forbidden_hits:
            failure_reason = f"forbidden keywords in output: {forbidden_hits}"
    # 4. Evidence path check — required sources must be non-empty in final_state["evidence"]
    if not failure_reason and answer_key.required_evidence_sources:
        evidence = final_state.get("evidence") or {}
        for source_key in answer_key.required_evidence_sources:
            if not evidence.get(source_key):
                failure_reason = f"required evidence not gathered: {source_key!r}"
                break

    passed = not failure_reason
    return ScenarioScore(
        scenario_id=fixture.scenario_id,
        passed=passed,
        root_cause_present=root_cause_present,
        expected_category=fixture.answer_key.root_cause_category,
        actual_category=actual_category,
        missing_keywords=missing_keywords,
        matched_keywords=matched_keywords,
        root_cause=root_cause,
        failure_reason=failure_reason,
    )


def run_scenario(
    fixture: ScenarioFixture,
    use_mock_grafana: bool = False,
) -> tuple[dict[str, Any], ScenarioScore]:
    alert = fixture.alert
    labels = alert.get("commonLabels", {}) or {}

    alert_name = str(alert.get("title") or labels.get("alertname") or fixture.scenario_id)
    pipeline_name = str(labels.get("pipeline_name") or "rds-postgres-synthetic")
    severity = str(labels.get("severity") or "critical")

    resolved_integrations = _build_resolved_integrations(fixture, use_mock_grafana)

    final_state = run_investigation(
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
        raw_alert=alert,
        resolved_integrations=resolved_integrations,
    )
    state_dict = dict(final_state)
    return state_dict, score_result(fixture, state_dict)


def run_suite(argv: list[str] | None = None) -> list[ScenarioScore]:
    args = parse_args(argv)
    fixtures = load_all_scenarios(SUITE_DIR)
    if args.scenario:
        fixtures = [fixture for fixture in fixtures if fixture.scenario_id == args.scenario]
        if not fixtures:
            raise SystemExit(f"Unknown scenario: {args.scenario}")

    results: list[ScenarioScore] = []
    for fixture in fixtures:
        _, score = run_scenario(fixture, use_mock_grafana=args.mock_grafana)
        results.append(score)

    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        for result in results:
            status = "PASS" if result.passed else "FAIL"
            detail = f"reason={result.failure_reason!r}" if result.failure_reason else f"category={result.actual_category}"
            print(f"{status} {result.scenario_id} {detail}")

        passed_count = sum(1 for result in results if result.passed)
        print(f"\nResults: {passed_count}/{len(results)} passed")

    return results


def main(argv: list[str] | None = None) -> int:
    results = run_suite(argv)
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
