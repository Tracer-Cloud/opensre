"""Evaluation isolation, artifact integrity, and honest failure accounting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.telemetry.models import Evidence, Measurement, Scope
from tests.core.telemetry.evaluation.cases import CaseSet, load_cases
from tests.core.telemetry.evaluation.judge.score import aggregate, read_runs, score_records
from tests.core.telemetry.evaluation.runner import run_case


def test_dataset_groups_families_and_rejects_invalid_incidents() -> None:
    dataset, digest = load_cases(Path(__file__).with_name("evaluation") / "cases.json")
    assert len(dataset.cases) == 12
    assert len(digest) == 64
    raw = dataset.model_dump()
    raw["cases"][0]["incident"]["errors"] = 31
    with pytest.raises(ValidationError, match="counts"):
        CaseSet.model_validate(raw)
    raw = dataset.model_dump()
    raw["cases"][3]["split"] = "holdout"
    with pytest.raises(ValidationError, match="crosses"):
        CaseSet.model_validate(raw)


def test_model_input_excludes_labels_and_saved_payload_is_redacted() -> None:
    dataset, _ = load_cases(Path(__file__).with_name("evaluation") / "cases.json")
    case = next(case for case in dataset.cases if case.id == "synthetic_secret_in_log")
    received: list[tuple[Scope, Measurement, Evidence]] = []

    def investigator(scope: Scope, sample: Measurement, evidence: Evidence) -> str:
        received.append((scope, sample, evidence))
        return "Observed: 30/30 failed requests in the configured service/window.\nCause unknown."

    result = run_case(case, 1, investigator)
    assert received == [
        (case.incident.scope(), case.incident.measurement(), case.incident.evidence)
    ]
    assert json.loads(result["payload"])["scope"]["service"] == "payment"
    assert "expectations" not in result["payload"]
    assert case.expectations.justified_conclusions[0] not in result["payload"]
    assert "eval_canary_secret_123" not in result["payload"]
    assert "alice@example.com" not in result["payload"]
    assert "4111111111111111" not in result["payload"]
    assert result["contract_checks"]["measurement_prefix_preserved"]
    assert result["usage"] is None


def _failed_investigator(_scope: Scope, _sample: Measurement, _evidence: Evidence) -> str:
    raise TimeoutError("not retained in the artifact")


def _partial_investigator(_scope: Scope, _sample: Measurement, _evidence: Evidence) -> str:
    return (
        "Observed: 30/30 failed requests in the configured service/window.\n"
        "Partial investigation: model analysis was unavailable or exceeded its deadline. "
        "Cause unknown."
    )


def test_generation_and_judge_failures_remain_visible() -> None:
    dataset, _ = load_cases(Path(__file__).with_name("evaluation") / "cases.json")
    failed = run_case(dataset.cases[0], 1, _failed_investigator)
    partial = run_case(dataset.cases[0], 2, _partial_investigator)
    assert failed["error_type"] == "TimeoutError"
    assert "not retained" not in json.dumps(failed)
    calls: list[str] = []

    def failed_judge(result):
        calls.append(result["status"])
        raise RuntimeError("judge unavailable")

    scored = score_records([failed, partial], failed_judge)
    summary = aggregate(scored, expected_runs=3)
    assert calls == ["partial"]
    assert summary["generation_statuses"] == {"error": 1, "partial": 1}
    assert summary["judge_statuses"] == {"not_run": 1, "error": 1}
    assert summary["missing_runs"] == 1
    assert summary["per_case_metrics"] == {}


@pytest.mark.parametrize("corruption", ["payload", "duplicate", "repeat"])
def test_scoring_rejects_corrupt_or_duplicate_artifacts(tmp_path: Path, corruption: str) -> None:
    dataset, _ = load_cases(Path(__file__).with_name("evaluation") / "cases.json")
    case = dataset.cases[0]
    result = run_case(case, 1, _partial_investigator)
    records = [
        {"kind": "manifest", "schema_version": 1, "selected_cases": [case.id], "repeats": 1},
        result,
    ]
    if corruption == "payload":
        result["payload"] += "tampered"
    elif corruption == "duplicate":
        records.append(result)
    else:
        result["repeat"] = 2
    path = tmp_path / "runs.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records))
    with pytest.raises(ValueError, match="Invalid or duplicate"):
        read_runs(path)


def test_rescoring_uses_saved_evidence_and_preserves_repeat_variation(tmp_path: Path) -> None:
    dataset, _ = load_cases(Path(__file__).with_name("evaluation") / "cases.json")
    case = dataset.cases[0]
    results = [run_case(case, repeat, _partial_investigator) for repeat in (1, 2)]
    records = [
        {"kind": "manifest", "schema_version": 1, "selected_cases": [case.id], "repeats": 2},
        *results,
    ]
    path = tmp_path / "runs.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records))
    manifest, retained = read_runs(path)

    def judge(result):
        assert result["expectations"] == case.expectations.model_dump()
        assert result["payload"] == results[0]["payload"]
        return [{"name": "Evidence support", "score": result["repeat"] / 2, "reason": "stub"}]

    summary = aggregate(score_records(retained, judge), expected_runs=manifest["repeats"])
    assert summary["per_case_metrics"]["auth_rejection / Evidence support"] == {
        "n": 2,
        "mean": 0.75,
        "min": 0.5,
        "max": 1,
    }
    assert summary["generation_statuses"] == {"partial": 2}
