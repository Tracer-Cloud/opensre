"""Calibration provenance, explicit human judgments and scorecard denominators."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tests.core.telemetry.evaluation.calibration import Worksheet, compare, prepare
from tests.core.telemetry.evaluation.judge.score import read_runs, score_records
from tests.core.telemetry.evaluation.reporting import read_scores, scorecard


def _pack(tmp_path: Path) -> Path:
    root = Path(__file__).with_name("evaluation")
    destination = tmp_path / "calibration"
    prepare(root / "cases.json", root / "calibration.json", destination, seed=42)
    return destination


def _fake_scores(pack: Path) -> Path:
    runs = read_runs(pack / "runs.jsonl")

    def judge(result: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "status": "completed",
                "score": result["repeat"] / 3,
                "reason": "offline test stub",
                "error_type": None,
            }
            for name in ("Evidence support", "Diagnostic usefulness")
        ]

    events = [
        {
            "kind": "score_manifest",
            "schema_version": 1,
            "runs_sha256": runs.sha256,
            "generation_manifest": runs.manifest,
            "judge_model": "offline-test-stub",
            "scorer_sha256": "unit-test-only",
        }
    ]
    score_records(runs.results, judge, events.append)
    events.append({"kind": "summary"})
    path = pack / "scores.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n")
    return path


def _review(pack: Path) -> dict[str, Any]:
    worksheet: dict[str, Any] = json.loads((pack / "review.json").read_text())
    worksheet["reviewer"] = "unit-test-only"
    for case in worksheet["cases"]:
        case["decision"] = "accepted"
    for rating in worksheet["ratings"]:
        rating.update(
            review_status="reviewed",
            evidence_support=3 - rating["repeat"],
            diagnostic_usefulness=3 - rating["repeat"],
            rationale="Synthetic test rating; not an SRE judgment",
        )
    return worksheet


def test_pack_keeps_author_notes_out_of_judge_and_review_inputs(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    runs = read_runs(pack / "runs.jsonl")
    worksheet = Worksheet.model_validate_json((pack / "review.json").read_bytes())
    assert runs.manifest["purpose"] == "judge_calibration"
    assert len(runs.results) == len(worksheet.ratings) == 9
    assert not worksheet.reviewer
    assert all(
        row.review_status == "pending" and row.evidence_support is None for row in worksheet.ratings
    )
    assert "author_note" not in (pack / "runs.jsonl").read_text()
    assert "author_note" not in (pack / "review.json").read_text()
    assert all(row["candidate_id"] not in row["payload"] for row in runs.results)
    with pytest.raises(FileExistsError):
        _pack(tmp_path)


def test_pending_reviews_do_not_produce_agreement(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    scores = _fake_scores(pack)
    result = compare(pack / "runs.jsonl", scores, pack / "review.json")
    assert result["eligible_reviewed_candidates"] == 0
    assert result["pending_or_unaccepted_candidates"] == 9
    assert result["comparisons"]["Evidence support"]["agreement_fraction"] is None
    with pytest.raises(ValueError, match="not investigator baseline"):
        runs = read_runs(pack / "runs.jsonl")
        scorecard(runs, read_scores(scores, runs))


def test_pairwise_disagreements_and_human_ties_are_explicit(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    scores = _fake_scores(pack)
    review = _review(pack)
    for rating in review["ratings"]:
        rating["diagnostic_usefulness"] = 2
    (pack / "review.json").write_text(json.dumps(review))
    result = compare(pack / "runs.jsonl", scores, pack / "review.json")
    support = result["comparisons"]["Evidence support"]
    assert support["comparable_pairs"] == len(support["disagreements"]) == 9
    assert support["agreement_fraction"] == 0
    usefulness = result["comparisons"]["Diagnostic usefulness"]
    assert usefulness["human_ties"] == 9
    assert usefulness["agreement_fraction"] is None


@pytest.mark.parametrize(
    "tampering", ["runs_hash", "report", "candidate", "case_context", "duplicate"]
)
def test_comparison_rejects_mismatched_review_material(tmp_path: Path, tampering: str) -> None:
    pack = _pack(tmp_path)
    scores = _fake_scores(pack)
    review = _review(pack)
    if tampering == "runs_hash":
        review["runs_sha256"] = "other"
    elif tampering == "report":
        review["ratings"][0]["report"] = "changed"
    elif tampering == "candidate":
        review["ratings"][0]["candidate_id"] = "other"
    elif tampering == "case_context":
        review["cases"][0]["payload"] = "changed"
    else:
        review["ratings"].append(review["ratings"][0])
    (pack / "review.json").write_text(json.dumps(review))
    with pytest.raises(ValueError):
        compare(pack / "runs.jsonl", scores, pack / "review.json")


def test_review_requires_attribution_and_complete_ratings(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    review = _review(pack)
    review["reviewer"] = ""
    with pytest.raises(ValidationError, match="reviewer"):
        Worksheet.model_validate(review)
    review["reviewer"] = "unit-test-only"
    review["ratings"][0]["evidence_support"] = None
    with pytest.raises(ValidationError, match="both scores"):
        Worksheet.model_validate(review)


def test_scorecard_retains_interrupted_metric_and_generation_failures(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    generation_path = pack / "runs.jsonl"
    records = [json.loads(line) for line in generation_path.read_text().splitlines()]
    records[0].pop("purpose")
    records[0]["report_origin"] = "offline_test_fixture"
    records[1]["status"] = "partial"
    generation_path.write_text("\n".join(json.dumps(row) for row in records) + "\n")
    scores_path = _fake_scores(pack)
    events = [json.loads(line) for line in scores_path.read_text().splitlines()]
    scores_path.write_text("\n".join(json.dumps(row) for row in events[:2]) + '\n{"kind":')
    runs = read_runs(generation_path)
    scores = read_scores(scores_path, runs)
    result = scorecard(runs, scores)
    assert result["generation_statuses"]["partial"] == 1
    assert result["judge_statuses"] == {"interrupted": 1}
    assert result["missing_judge_attempts"] == 8
    assert result["scores_incomplete_tail"]
    assert not result["scoring_finished"]
    group = result["by_failure_class"][runs.results[0]["category"]]
    assert group["judgments"]["per_case_metrics"]


@pytest.mark.parametrize("tampering", ["hash", "duplicate_metric", "result_metrics", "nan"])
def test_score_reader_rejects_mismatched_or_fabricated_scores(
    tmp_path: Path, tampering: str
) -> None:
    pack = _pack(tmp_path)
    scores_path = _fake_scores(pack)
    records = [json.loads(line) for line in scores_path.read_text().splitlines()]
    if tampering == "hash":
        records[0]["runs_sha256"] = "other"
    elif tampering == "duplicate_metric":
        records.insert(2, records[1])
    elif tampering == "result_metrics":
        records[3]["metrics"][0]["score"] = 0
    else:
        records[1]["score"] = float("nan")
    scores_path.write_text("\n".join(json.dumps(row) for row in records))
    with pytest.raises(ValueError):
        read_scores(scores_path, read_runs(pack / "runs.jsonl"))
