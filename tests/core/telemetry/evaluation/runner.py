"""Generate replay reports through the production one-shot entrypoint."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import platform
import subprocess
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.telemetry import investigation
from core.telemetry.models import Evidence, Measurement, Scope
from tests.core.telemetry.evaluation.cases import IncidentCase, load_cases
from tests.core.telemetry.evaluation.judge.score import read_runs


def run_case(
    case: IncidentCase,
    repeat: int,
    investigator: Callable[[Scope, Measurement, Evidence], str] = investigation.investigate,
) -> dict[str, Any]:
    """Only incident input crosses the investigator boundary; labels stay outside it."""
    incident = case.incident
    scope, measurement = incident.scope(), incident.measurement()
    payload = investigation.evidence_payload(scope, measurement, incident.evidence)
    started = time.monotonic()
    report, error_type, status = "", None, "completed"
    try:
        report = investigator(scope, measurement, incident.evidence)
        if (
            "Partial investigation: model analysis was unavailable or exceeded its deadline."
            in report
        ):
            status = "partial"
    except Exception as exc:
        status, error_type = "error", type(exc).__name__
    prefix = (
        f"Observed: {measurement.errors:g}/{measurement.total:g} failed requests "
        "in the configured service/window.\n"
    )
    return {
        "kind": "result",
        "case_id": case.id,
        "category": case.category,
        "family": case.family,
        "split": case.split,
        "review_status": case.review_status,
        "repeat": repeat,
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "expectations": case.expectations.model_dump(),
        "report": report,
        "status": status,
        "error_type": error_type,
        "duration_seconds": time.monotonic() - started,
        "contract_checks": {"measurement_prefix_preserved": report.startswith(prefix)},
        "usage": None,
    }


def provenance(dataset_sha256: str) -> dict[str, Any]:
    """Record source identity and resolved route without serializing credentials/settings."""
    from core.llm.client_builders import build_agent_client

    route = investigation._checked_route()
    client = build_agent_client(route)
    root = Path(__file__).resolve().parents[4]
    source = inspect.getsource(investigation)
    synthesis_source = inspect.getsource(investigation._model_report)
    return {
        "kind": "manifest",
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "working_tree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True)
        ),
        "dataset_sha256": dataset_sha256,
        "investigator_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "synthesis_source_sha256": hashlib.sha256(synthesis_source.encode()).hexdigest(),
        "synthesis_source": synthesis_source,
        "provider": route.provider,
        "resolved_model": client.model_id,
        "generation_settings": "production defaults; no evaluation overrides",
        "python": platform.python_version(),
    }


def generate(args: argparse.Namespace) -> int:
    cases, dataset_hash = load_cases(args.cases)
    selected = [
        case
        for case in cases.cases
        if (args.split == "all" or case.split == args.split)
        and (args.case is None or case.id == args.case)
    ]
    if not selected or args.repeats < 1:
        raise ValueError("Select at least one case and a positive repeat count")
    manifest = provenance(dataset_hash)
    manifest.update({"selected_cases": [case.id for case in selected], "repeats": args.repeats})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    failures = 0
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write(json.dumps(manifest) + "\n")
        output.flush()
        for case in selected:
            for repeat in range(1, args.repeats + 1):
                result = run_case(case, repeat)
                output.write(json.dumps(result) + "\n")
                output.flush()
                failures += result["status"] != "completed"
                print(f"{case.id} repeat={repeat} status={result['status']}", flush=True)
    return int(failures > 0)


def summarize(path: Path) -> None:
    manifest, results = read_runs(path)
    expected = len(manifest["selected_cases"]) * manifest["repeats"]
    print(
        json.dumps(
            {
                "expected_runs": expected,
                "recorded_runs": len(results),
                "missing_runs": expected - len(results),
                "statuses": dict(Counter(result["status"] for result in results)),
                "measurement_prefix_failures": sum(
                    not result["contract_checks"]["measurement_prefix_preserved"]
                    for result in results
                ),
                "semantic_scores": "Run the isolated judge; offline summary does not score cause.",
            },
            indent=2,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate_parser = commands.add_parser("generate", help="Calls the configured API model")
    generate_parser.add_argument(
        "--cases", type=Path, default=Path(__file__).with_name("cases.json")
    )
    generate_parser.add_argument(
        "--split", choices=["development", "holdout", "all"], default="development"
    )
    generate_parser.add_argument("--case", help="Optional exact case ID")
    generate_parser.add_argument("--repeats", type=int, default=3)
    generate_parser.add_argument(
        "--output", type=Path, required=True, help="New JSONL file; never overwritten"
    )
    summary_parser = commands.add_parser("summarize", help="Offline execution-status summary")
    summary_parser.add_argument("runs", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "generate":
            return generate(args)
        summarize(args.runs)
        return 0
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Evaluation failed: {type(exc).__name__}\n")


if __name__ == "__main__":
    raise SystemExit(main())
