"""Prepare neutral calibration reports and compare reviewed human/judge preferences."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from core.telemetry.investigation import evidence_payload, sanitize
from tests.core.telemetry.evaluation.cases import Contract, load_cases
from tests.core.telemetry.evaluation.judge.score import read_runs
from tests.core.telemetry.evaluation.reporting import read_scores, write_json


class Example(Contract):
    case_id: str
    report: str = Field(min_length=1)
    author_note: str = Field(min_length=1)


class ExampleSet(Contract):
    version: Literal[1]
    review_status: Literal["pending_sre_review"]
    examples: list[Example] = Field(min_length=1)


class Rating(Contract):
    candidate_id: str
    case_id: str
    repeat: int = Field(ge=1)
    report: str
    review_status: Literal["pending", "reviewed"] = "pending"
    evidence_support: Annotated[int, Field(strict=True, ge=0, le=3)] | None = None
    diagnostic_usefulness: Annotated[int, Field(strict=True, ge=0, le=3)] | None = None
    rationale: str = ""

    @model_validator(mode="after")
    def completed_rating(self) -> Rating:
        if self.review_status == "reviewed" and (
            self.evidence_support is None
            or self.diagnostic_usefulness is None
            or not self.rationale.strip()
        ):
            raise ValueError("Reviewed ratings need both scores and a rationale")
        return self


class CaseReview(Contract):
    case_id: str
    proposed_expectations: dict[str, Any]
    payload: str
    decision: Literal["pending", "accepted", "revise"] = "pending"
    notes: str = ""


class Worksheet(Contract):
    version: Literal[1]
    runs_sha256: str
    reviewer: str = ""
    cases: list[CaseReview]
    ratings: list[Rating]

    @model_validator(mode="after")
    def attributable_reviews(self) -> Worksheet:
        if (
            any(row.review_status == "reviewed" for row in self.ratings)
            or any(case.decision != "pending" for case in self.cases)
        ) and not self.reviewer.strip():
            raise ValueError("Human reviews must identify their reviewer")
        return self


def prepare(cases_path: Path, examples_path: Path, output: Path, seed: int) -> None:
    cases, dataset_hash = load_cases(cases_path)
    examples_raw = examples_path.read_bytes()
    examples = ExampleSet.model_validate_json(examples_raw)
    sources = {case.id: case for case in cases.cases}
    grouped: dict[str, list[Example]] = {}
    for example in examples.examples:
        if example.case_id not in sources or sources[example.case_id].split != "development":
            raise ValueError("Calibration examples must reference development cases")
        grouped.setdefault(example.case_id, []).append(example)
    counts = {len(rows) for rows in grouped.values()}
    if len(counts) != 1:
        raise ValueError("Each calibration case needs the same number of candidate reports")
    records: list[dict[str, Any]] = []
    ratings: list[Rating] = []
    case_reviews: list[CaseReview] = []
    rng = random.Random(seed)
    seen: set[str] = set()
    for case_id, candidates in sorted(grouped.items()):
        source = sources[case_id]
        incident = source.incident
        payload = evidence_payload(incident.scope(), incident.measurement(), incident.evidence)
        case_reviews.append(
            CaseReview(
                case_id=case_id,
                payload=payload,
                proposed_expectations=source.expectations.model_dump(),
            )
        )
        rng.shuffle(candidates)
        for repeat, example in enumerate(candidates, start=1):
            report = sanitize(example.report)
            candidate_id = hashlib.sha256((case_id + "\n" + report).encode()).hexdigest()[:16]
            if candidate_id in seen:
                raise ValueError("Duplicate calibration candidate")
            seen.add(candidate_id)
            prefix = f"Observed: {incident.errors:g}/{incident.total:g} failed requests in the configured service/window.\n"
            records.append(
                {
                    "kind": "result",
                    "case_id": case_id,
                    "repeat": repeat,
                    "candidate_id": candidate_id,
                    "category": source.category,
                    "family": source.family,
                    "split": source.split,
                    "review_status": source.review_status,
                    "payload": payload,
                    "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                    "expectations": source.expectations.model_dump(),
                    "report": report,
                    "status": "completed",
                    "error_type": None,
                    "duration_seconds": 0,
                    "usage": None,
                    "contract_checks": {"measurement_prefix_preserved": report.startswith(prefix)},
                }
            )
            ratings.append(
                Rating(candidate_id=candidate_id, case_id=case_id, repeat=repeat, report=report)
            )
    manifest = {
        "kind": "manifest",
        "schema_version": 1,
        "purpose": "judge_calibration",
        "report_origin": "authored_examples_not_investigator_outputs",
        "created_at": datetime.now(UTC).isoformat(),
        "selected_cases": sorted(grouped),
        "repeats": next(iter(counts)),
        "seed": seed,
        "dataset_sha256": dataset_hash,
        "examples_sha256": hashlib.sha256(examples_raw).hexdigest(),
    }
    raw = ("\n".join(json.dumps(row) for row in [manifest, *records]) + "\n").encode()
    worksheet = Worksheet(
        version=1, runs_sha256=hashlib.sha256(raw).hexdigest(), cases=case_reviews, ratings=ratings
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(mode=0o700)  # A new directory prevents accidental mixing of packs.
    fd = os.open(output / "runs.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
    write_json(output / "review.json", worksheet.model_dump())


def compare(runs_path: Path, scores_path: Path, review_path: Path) -> dict[str, Any]:
    runs = read_runs(runs_path)
    if runs.manifest.get("purpose") != "judge_calibration":
        raise ValueError("Expected an authored calibration pack")
    scores = read_scores(scores_path, runs)
    review_raw = review_path.read_bytes()
    worksheet = Worksheet.model_validate_json(review_raw)
    if worksheet.runs_sha256 != runs.sha256:
        raise ValueError("Human worksheet does not match the calibration pack")
    originals = {(row["case_id"], row["repeat"]): row for row in runs.results}
    reviewed: dict[tuple[str, int], Rating] = {}
    for rating in worksheet.ratings:
        key = rating.case_id, rating.repeat
        if (
            key not in originals
            or key in reviewed
            or rating.report != originals[key]["report"]
            or rating.candidate_id != originals[key]["candidate_id"]
        ):
            raise ValueError("Changed, unknown or duplicate human candidate")
        reviewed[key] = rating
    if reviewed.keys() != originals.keys():
        raise ValueError("Human worksheet must retain pending candidates")
    source_cases = {row["case_id"]: row for row in runs.results}
    case_reviews: dict[str, CaseReview] = {}
    for case in worksheet.cases:
        source = source_cases.get(case.case_id)
        if (
            source is None
            or case.case_id in case_reviews
            or case.payload != source["payload"]
            or case.proposed_expectations != source["expectations"]
        ):
            raise ValueError("Changed, unknown or duplicate case review")
        case_reviews[case.case_id] = case
    if case_reviews.keys() != source_cases.keys():
        raise ValueError("Missing case reviews")
    metrics = {
        (row["case_id"], row["repeat"], metric["name"]): metric["score"]
        for row in scores.results
        for metric in row["metrics"]
        if metric["status"] == "completed"
    }
    eligible = [
        row
        for row in worksheet.ratings
        if row.review_status == "reviewed" and case_reviews[row.case_id].decision == "accepted"
    ]
    comparisons: dict[str, Any] = {}
    for field, metric_name in (
        ("evidence_support", "Evidence support"),
        ("diagnostic_usefulness", "Diagnostic usefulness"),
    ):
        stats: dict[str, Any] = {
            "comparable_pairs": 0,
            "agreements": 0,
            "human_ties": 0,
            "judge_missing_pairs": 0,
            "judge_ties": 0,
            "disagreements": [],
        }
        for left, right in combinations(eligible, 2):
            if left.case_id != right.case_id:
                continue
            human_left, human_right = getattr(left, field), getattr(right, field)
            if human_left == human_right:
                stats["human_ties"] += 1
                continue
            jl = metrics.get((left.case_id, left.repeat, metric_name))
            jr = metrics.get((right.case_id, right.repeat, metric_name))
            if jl is None or jr is None:
                stats["judge_missing_pairs"] += 1
                continue
            stats["comparable_pairs"] += 1
            if jl == jr:
                stats["judge_ties"] += 1
            if (human_left - human_right) * (jl - jr) > 0:
                stats["agreements"] += 1
            else:
                stats["disagreements"].append(
                    {
                        "case_id": left.case_id,
                        "left": left.candidate_id,
                        "right": right.candidate_id,
                        "human_scores": [human_left, human_right],
                        "judge_scores": [jl, jr],
                    }
                )
        stats["agreement_fraction"] = (
            stats["agreements"] / stats["comparable_pairs"] if stats["comparable_pairs"] else None
        )
        comparisons[metric_name] = stats
    return {
        "status": "advisory_calibration_comparison",
        "reviewer": worksheet.reviewer,
        "runs_sha256": runs.sha256,
        "scores_sha256": scores.sha256,
        "review_sha256": hashlib.sha256(review_raw).hexdigest(),
        "judge_model": scores.manifest["judge_model"],
        "scorer_sha256": scores.manifest["scorer_sha256"],
        "candidate_count": len(worksheet.ratings),
        "eligible_reviewed_candidates": len(eligible),
        "pending_or_unaccepted_candidates": len(worksheet.ratings) - len(eligible),
        "scoring_finished": scores.finished,
        "comparisons": comparisons,
        "limits": [
            "Authored example ratings are not investigator accuracy.",
            "Within-case pairs are correlated; inspect counts and disagreements, not only the fraction.",
            "This command does not change fixture review status or validate ground truth.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pack = commands.add_parser("prepare", help="Offline; creates a new review directory")
    pack.add_argument("--cases", type=Path, default=Path(__file__).with_name("cases.json"))
    pack.add_argument("--examples", type=Path, default=Path(__file__).with_name("calibration.json"))
    pack.add_argument("--seed", type=int, default=42)
    pack.add_argument("--output", type=Path, required=True)
    comparison = commands.add_parser("compare", help="Offline; requires explicit human ratings")
    for name in ("runs", "scores", "review", "output"):
        comparison.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.cases, args.examples, args.output, args.seed)
    else:
        write_json(args.output, compare(args.runs, args.scores, args.review))


if __name__ == "__main__":
    main()
