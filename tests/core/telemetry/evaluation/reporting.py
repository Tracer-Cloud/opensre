"""Validate saved judge checkpoints and build failure-class scorecards offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from tests.core.telemetry.evaluation.judge.score import RunSnapshot, aggregate, read_runs


@dataclass(frozen=True)
class ScoreSnapshot:
    manifest: dict[str, Any]
    results: list[dict[str, Any]]
    sha256: str
    incomplete_tail: bool
    finished: bool


def read_scores(path: Path, runs: RunSnapshot) -> ScoreSnapshot:
    """Bind score checkpoints to the exact generation snapshot and retain interrupted work."""
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    events: list[dict[str, Any]] = []
    incomplete = False
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            if index != len(lines) - 1 or line.endswith((b"\n", b"\r")) or not events:
                raise ValueError("Damaged score JSONL row") from None
            incomplete = True
            continue
        if not isinstance(event, dict):
            raise ValueError("Score events must be objects")
        events.append(event)
    if not events:
        raise ValueError("Missing score manifest")
    manifest, *events = events
    if (
        manifest.get("kind") != "score_manifest"
        or manifest.get("schema_version") != 1
        or manifest.get("runs_sha256") != runs.sha256
        or manifest.get("generation_manifest") != runs.manifest
    ):
        raise ValueError("Scores do not match the generation snapshot")
    inputs = {(row["case_id"], row["repeat"]): row for row in runs.results}
    checkpoints: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    complete: dict[tuple[str, int], dict[str, Any]] = {}
    finished = False
    for event in events:
        if finished:
            raise ValueError("Events follow the final summary")
        if event.get("kind") == "summary":
            finished = True
            continue  # Recompute statistics from records, never trust a stored summary.
        key = event.get("case_id"), event.get("repeat")
        if key not in inputs or key in complete:
            raise ValueError("Unknown or duplicate score attempt")
        metrics = checkpoints.setdefault(key, {})
        if event.get("kind") == "metric":
            metric: dict[str, Any] = {
                name: event.get(name)
                for name in ("name", "status", "score", "reason", "error_type")
            }
            validate_metric(metric)
            if metric["name"] in metrics:
                raise ValueError("Duplicate metric checkpoint")
            metrics[metric["name"]] = metric
        elif event.get("kind") == "result":
            declared = event.get("metrics", [])
            for metric in declared:
                validate_metric(metric)
            if (
                len(declared) != len(metrics)
                or {metric["name"]: metric for metric in declared} != metrics
            ):
                raise ValueError("Result does not match its metric checkpoints")
            source = inputs[key]
            if (
                event.get("generation_status") != source["status"]
                or event.get("contract_checks") != source["contract_checks"]
            ):
                raise ValueError("Score result changed generation facts")
            judge_status = event.get("judge_status")
            if judge_status not in {"completed", "error", "not_run"}:
                raise ValueError("Unknown judge status")
            if judge_status == "completed" and (
                len(metrics) != 2 or any(metric["status"] != "completed" for metric in declared)
            ):
                raise ValueError("Completed judgment requires both successful metrics")
            if (source["status"] == "error") != (judge_status == "not_run") or (
                judge_status == "not_run" and declared
            ):
                raise ValueError("Invalid skipped judgment")
            complete[key] = event
        else:
            raise ValueError("Unknown score event")
    for key, metrics in checkpoints.items():
        if key not in complete:
            source = inputs[key]
            complete[key] = {
                "case_id": key[0],
                "repeat": key[1],
                "generation_status": source["status"],
                "contract_checks": source["contract_checks"],
                "metrics": list(metrics.values()),
                "judge_status": "interrupted",
                "judge_error_type": None,
            }
    return ScoreSnapshot(
        manifest, list(complete.values()), hashlib.sha256(raw).hexdigest(), incomplete, finished
    )


def validate_metric(metric: dict[str, Any]) -> None:
    score = metric.get("score")
    if metric.get("name") not in {"Evidence support", "Diagnostic usefulness"}:
        raise ValueError("Unknown metric")
    if metric.get("status") == "completed":
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
            or not 0 <= score <= 1
        ):
            raise ValueError("Invalid normalized metric score")
    elif metric.get("status") != "error" or score is not None:
        raise ValueError("Invalid failed metric")


def scorecard(runs: RunSnapshot, scores: ScoreSnapshot) -> dict[str, Any]:
    """Show operational completion alongside advisory semantic scores."""
    if (
        runs.manifest.get("purpose") == "judge_calibration"
        or runs.manifest.get("report_origin") == "authored_examples_not_investigator_outputs"
    ):
        raise ValueError("Authored calibration reports are not investigator baseline results")
    groups: dict[str, dict[str, Any]] = {}
    for category in sorted({row["category"] for row in runs.results}):
        rows = [row for row in runs.results if row["category"] == category]
        identities = {(row["case_id"], row["repeat"]) for row in rows}
        judged = [row for row in scores.results if (row["case_id"], row["repeat"]) in identities]
        judgments = aggregate(judged, len(rows))
        # Generation facts use every recorded attempt, including those never judged.
        judgments.pop("generation_statuses")
        judgments.pop("measurement_prefix_failures")
        groups[category] = {
            "recorded_runs": len(rows),
            "case_ids": sorted({row["case_id"] for row in rows}),
            "generation_statuses": dict(Counter(row["status"] for row in rows)),
            "measurement_prefix_failures": sum(
                not row["contract_checks"]["measurement_prefix_preserved"] for row in rows
            ),
            "duration_seconds": {
                "mean": mean(row["duration_seconds"] for row in rows),
                "max": max(row["duration_seconds"] for row in rows),
            },
            "judgments": judgments,
        }
    expected = len(runs.manifest["selected_cases"]) * runs.manifest["repeats"]
    fixture_statuses = {row["case_id"]: row["review_status"] for row in runs.results}
    return {
        "status": "advisory_unvalidated_baseline",
        "runs_sha256": runs.sha256,
        "scores_sha256": scores.sha256,
        "generation_manifest": runs.manifest,
        "judge_model": scores.manifest["judge_model"],
        "scorer_sha256": scores.manifest["scorer_sha256"],
        "expected_runs": expected,
        "expected_cases": len(runs.manifest["selected_cases"]),
        "recorded_cases": len(fixture_statuses),
        "recorded_runs": len(runs.results),
        "missing_generation_runs": expected - len(runs.results),
        "missing_judge_attempts": len(runs.results) - len(scores.results),
        "input_incomplete_tail": runs.incomplete_tail,
        "scores_incomplete_tail": scores.incomplete_tail,
        "scoring_finished": scores.finished,
        "fixture_review_statuses": dict(Counter(fixture_statuses.values())),
        "generation_statuses": dict(Counter(row["status"] for row in runs.results)),
        "measurement_prefix_failures": sum(
            not row["contract_checks"]["measurement_prefix_preserved"] for row in runs.results
        ),
        "judge_statuses": dict(Counter(row["judge_status"] for row in scores.results)),
        "by_failure_class": groups,
        "limits": [
            "No semantic pass threshold or production accuracy claim.",
            "Unsupported claims and abstention require SRE claim-level review; scores are not verified claim counts.",
        ],
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(value, output, indent=2)
        output.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New JSON scorecard file")
    args = parser.parse_args()
    runs = read_runs(args.runs)
    write_json(args.output, scorecard(runs, read_scores(args.scores, runs)))


if __name__ == "__main__":
    main()
