from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from app.pipeline.runners import run_investigation
from tests.synthetic.eks.scenario_loader import (
    SUITE_DIR,
    K8sScenarioFixture,
    load_all_scenarios,
)
from tests.synthetic.mock_datadog_backend.backend import FixtureDatadogBackend
from tests.synthetic.mock_eks_backend.backend import FixtureEKSBackend

# Maps fixture schema evidence keys to the agent's internal state keys.
# Kept identity for the initial harness drop; refine as scenarios #261+ land
# and we learn which state keys the K8s pipeline actually populates.
_EVIDENCE_KEY_MAP: dict[str, str] = {
    "eks_pods": "eks_pods",
    "eks_events": "eks_events",
    "eks_deployments": "eks_deployments",
    "eks_node_health": "eks_node_health",
    "eks_pod_logs": "eks_pod_logs",
    "datadog_logs": "datadog_logs",
    "datadog_monitors": "datadog_monitors",
}


@dataclass(frozen=True)
class TrajectoryScore:
    actual_sequence: list[str]  # flattened actions from executed_hypotheses
    expected_sequence: list[str]  # from answer_key.optimal_trajectory
    loops_used: int
    max_loops: int
    # Set-membership check: every expected action appears somewhere in actual.
    # Ordering is intentionally not enforced — actions execute in parallel and
    # completion order is non-deterministic.
    sequencing_ok: bool
    calibration_ok: bool  # loops_used <= max_loops
    efficiency_score: float  # mean(sequencing_ok, calibration_ok)


@dataclass(frozen=True)
class ReasoningScore:
    """Axis 2 adversarial reasoning quality score.

    ruling_out_ok: every ruling_out_keywords token was found in agent output.
    queries_ok: every required_queries token was requested via a tool call.
    reasoning_score: mean(ruling_out_ok, queries_ok); 1.0 = full pass.
    """

    ruling_out_ok: bool
    queries_ok: bool
    missing_ruling_out: list[str]
    missing_queries: list[str]
    reasoning_score: float


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
    trajectory: TrajectoryScore | None = None
    reasoning: ReasoningScore | None = None


@dataclass(frozen=True)
class ResolvedBackends:
    """Container for pre-built backends passed into ``run_scenario``."""

    eks: Any = None
    datadog: Any = None
    queried_tools: list[str] = field(default_factory=list)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the synthetic Kubernetes RCA suite.")
    parser.add_argument(
        "--scenario",
        default="",
        help="Run a single scenario directory name, e.g. 000-healthy.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON results.",
    )
    parser.add_argument(
        "--mock-backends",
        action="store_true",
        dest="mock_backends",
        help="Serve fixture data via FixtureEKSBackend + FixtureDatadogBackend "
        "instead of real EKS/Datadog calls.",
    )
    return parser.parse_args(argv)


def _build_resolved_integrations(
    fixture: K8sScenarioFixture,
    use_mock_backends: bool,
    eks_backend: Any = None,
    datadog_backend: Any = None,
) -> dict[str, Any] | None:
    """Build pre-resolved integrations to inject into run_investigation.

    Accepts optional pre-built backends so callers can instrument them
    (e.g. SelectiveEKSBackend for Axis 2) before injection.  Falls back to
    fresh fixture-backed backends when use_mock_backends=True and no backend
    is provided.

    EKS integrations live under the ``aws`` key (not ``eks``) because
    ``detect_sources`` reads the raw integration dict from
    ``resolved_integrations["aws"]`` and then emits a derived
    ``sources["eks"]`` that the EKS tools consume.  The injected ``_backend``
    propagates through that transform.
    """
    if not use_mock_backends and eks_backend is None and datadog_backend is None:
        return None

    resolved_eks = eks_backend
    if resolved_eks is None and use_mock_backends:
        resolved_eks = FixtureEKSBackend(fixture)

    resolved_datadog = datadog_backend
    if resolved_datadog is None and use_mock_backends:
        resolved_datadog = FixtureDatadogBackend(fixture)

    integrations: dict[str, Any] = {}
    if resolved_eks is not None:
        integrations["aws"] = {
            "role_arn": "",
            "external_id": "",
            "region": fixture.metadata.region,
            "cluster_names": [fixture.metadata.cluster_name],
            "_backend": resolved_eks,
        }
    if resolved_datadog is not None:
        integrations["datadog"] = {
            "api_key": "",
            "app_key": "",
            "site": "datadoghq.com",
            "_backend": resolved_datadog,
        }

    return integrations or None


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def score_trajectory(
    fixture: K8sScenarioFixture,
    final_state: dict[str, Any],
) -> TrajectoryScore | None:
    """Score the agent's investigation trajectory against the expected sequence.

    Returns None when no optimal_trajectory is declared for the scenario.
    """
    expected = list(fixture.answer_key.optimal_trajectory)
    if not expected:
        return None

    max_loops = fixture.answer_key.max_investigation_loops

    executed_hypotheses: list[dict[str, Any]] = final_state.get("executed_hypotheses") or []
    actual_sequence: list[str] = []
    for hyp in executed_hypotheses:
        for action in hyp.get("actions", []):
            actual_sequence.append(action)

    loops_used: int = int(final_state.get("investigation_loop_count") or len(executed_hypotheses))

    # Every expected action must appear somewhere in actual_sequence.  The check
    # is set-membership, not positional: when a real LLM skips a required action
    # entirely, this flips to False.  See the TrajectoryScore docstring above for
    # the rationale for ignoring order.
    sequencing_ok = set(expected) <= set(actual_sequence)
    calibration_ok = loops_used <= max_loops
    efficiency_score = (int(sequencing_ok) + int(calibration_ok)) / 2.0

    return TrajectoryScore(
        actual_sequence=actual_sequence,
        expected_sequence=expected,
        loops_used=loops_used,
        max_loops=max_loops,
        sequencing_ok=sequencing_ok,
        calibration_ok=calibration_ok,
        efficiency_score=efficiency_score,
    )


def score_reasoning(
    fixture: K8sScenarioFixture,
    final_state: dict[str, Any],
    queried_tools: list[str] | None = None,
) -> ReasoningScore | None:
    """Score Axis 2 adversarial reasoning quality.

    Returns None when neither ruling_out_keywords nor required_queries are
    declared for the scenario.

    Args:
        fixture: The scenario fixture containing the answer key.
        final_state: The agent's final investigation state dict.
        queried_tools: List of tool identifiers the agent invoked (from
            SelectiveEKSBackend.queried_tools and
            SelectiveDatadogBackend.queried_tools).  Pass None or [] when the
            backends do not record queries (Axis 1).
    """
    has_ruling_out = bool(fixture.answer_key.ruling_out_keywords)
    has_required_queries = bool(fixture.answer_key.required_queries)
    if not has_ruling_out and not has_required_queries:
        return None

    evidence_text = " ".join(
        [
            str(final_state.get("root_cause") or ""),
            " ".join(claim.get("claim", "") for claim in final_state.get("validated_claims", [])),
            " ".join(
                claim.get("claim", "") for claim in final_state.get("non_validated_claims", [])
            ),
            " ".join(final_state.get("causal_chain", [])),
        ]
    )
    normalized_output = _normalize_text(evidence_text)

    missing_ruling_out: list[str] = []
    if has_ruling_out:
        for token in fixture.answer_key.ruling_out_keywords:
            if token.lower() not in normalized_output:
                missing_ruling_out.append(token)

    missing_queries: list[str] = []
    if has_required_queries:
        audited = queried_tools or []
        for required in fixture.answer_key.required_queries:
            token = required.lower()
            if not any(token in q.lower() for q in audited):
                missing_queries.append(required)

    ruling_out_ok = not missing_ruling_out
    queries_ok = not missing_queries
    reasoning_score = (int(ruling_out_ok) + int(queries_ok)) / 2.0

    return ReasoningScore(
        ruling_out_ok=ruling_out_ok,
        queries_ok=queries_ok,
        missing_ruling_out=missing_ruling_out,
        missing_queries=missing_queries,
        reasoning_score=reasoning_score,
    )


def score_result(
    fixture: K8sScenarioFixture,
    final_state: dict[str, Any],
    queried_tools: list[str] | None = None,
) -> ScenarioScore:
    root_cause = str(final_state.get("root_cause") or "").strip()
    actual_category = str(final_state.get("root_cause_category") or "unknown").strip()
    root_cause_present = bool(root_cause and root_cause.lower() != "unable to determine root cause")

    evidence_text = " ".join(
        [
            root_cause,
            " ".join(claim.get("claim", "") for claim in final_state.get("validated_claims", [])),
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

    if not root_cause_present:
        failure_reason = "no root cause in output"
    elif actual_category != answer_key.root_cause_category:
        failure_reason = (
            f"wrong category: got {actual_category!r}, expected {answer_key.root_cause_category!r}"
        )
    elif missing_keywords:
        failure_reason = f"missing required keywords: {missing_keywords}"
    elif answer_key.forbidden_categories and actual_category in answer_key.forbidden_categories:
        failure_reason = f"forbidden category in output: {actual_category!r}"
    elif answer_key.forbidden_keywords:
        forbidden_hits = [
            kw for kw in answer_key.forbidden_keywords if _normalize_text(kw) in normalized_output
        ]
        if forbidden_hits:
            failure_reason = f"forbidden keywords in output: {forbidden_hits}"

    if not failure_reason and answer_key.required_evidence_sources:
        evidence = final_state.get("evidence") or {}
        for source_key in answer_key.required_evidence_sources:
            state_key = _EVIDENCE_KEY_MAP.get(source_key, source_key)
            if not evidence.get(state_key):
                failure_reason = f"required evidence not gathered: {source_key!r}"
                break

    passed = not failure_reason
    trajectory = score_trajectory(fixture, final_state)
    reasoning = score_reasoning(fixture, final_state, queried_tools)
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
        trajectory=trajectory,
        reasoning=reasoning,
    )


def _collect_queried_tools(eks_backend: Any, datadog_backend: Any) -> list[str]:
    """Collect audit-log entries from any Selective* backends that were injected."""
    tools: list[str] = []
    if eks_backend is not None and hasattr(eks_backend, "queried_tools"):
        tools.extend(list(eks_backend.queried_tools))
    if datadog_backend is not None and hasattr(datadog_backend, "queried_tools"):
        tools.extend(list(datadog_backend.queried_tools))
    return tools


def run_scenario(
    fixture: K8sScenarioFixture,
    use_mock_backends: bool = False,
    eks_backend: Any = None,
    datadog_backend: Any = None,
) -> tuple[dict[str, Any], ScenarioScore]:
    alert = fixture.alert
    labels = alert.get("commonLabels", {}) or {}

    alert_name = str(alert.get("title") or labels.get("alertname") or fixture.scenario_id)
    pipeline_name = str(labels.get("pipeline_name") or "k8s-eks-synthetic")
    severity = str(labels.get("severity") or "critical")

    resolved_integrations = _build_resolved_integrations(
        fixture,
        use_mock_backends,
        eks_backend=eks_backend,
        datadog_backend=datadog_backend,
    )

    final_state = run_investigation(
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
        raw_alert=alert,
        resolved_integrations=resolved_integrations,
    )
    state_dict = dict(final_state)

    queried_tools = _collect_queried_tools(eks_backend, datadog_backend)
    return state_dict, score_result(fixture, state_dict, queried_tools=queried_tools)


def run_suite(argv: list[str] | None = None) -> list[ScenarioScore]:
    args = parse_args(argv)
    fixtures = load_all_scenarios(SUITE_DIR)
    if args.scenario:
        fixtures = [fixture for fixture in fixtures if fixture.scenario_id == args.scenario]
        if not fixtures:
            raise SystemExit(f"Unknown scenario: {args.scenario}")

    results: list[ScenarioScore] = []
    for fixture in fixtures:
        _, score = run_scenario(fixture, use_mock_backends=args.mock_backends)
        results.append(score)

    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        for result in results:
            status = "PASS" if result.passed else "FAIL"
            detail = (
                f"reason={result.failure_reason!r}"
                if result.failure_reason
                else f"category={result.actual_category}"
            )
            print(f"{status} {result.scenario_id} {detail}")

        passed_count = sum(1 for result in results if result.passed)
        print(f"\nResults: {passed_count}/{len(results)} passed")

    return results


def main(argv: list[str] | None = None) -> int:
    results = run_suite(argv)
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
