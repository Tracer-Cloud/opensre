#!/usr/bin/env python3
"""Run RCA investigations from markdown alert files in tests/e2e/rca/.

Usage:
    python -m tests.e2e.rca.run_rca_test                    # run all .md files
    python -m tests.e2e.rca.run_rca_test pipeline_error_in_logs  # run one (with or without .md)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from app.pipeline.runners import run_investigation

RCA_DIR = Path(__file__).parent


def _parse_alert_md(path: Path) -> dict[str, Any]:
    """Extract title, severity, pipeline_name, and raw_alert JSON from a markdown alert file."""
    text = path.read_text()

    title_match = re.search(r"^#\s+Alert:\s+(.+)$", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem

    meta_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    meta: dict[str, Any] = json.loads(meta_match.group(1)) if meta_match else {}

    labels = meta.get("commonLabels", {})
    severity = labels.get("severity", "critical")
    pipeline_name = labels.get("pipeline_name") or labels.get("grafana_folder") or "unknown"

    return {"title": title, "severity": severity, "pipeline_name": pipeline_name, "raw_alert": meta}


def run_file(path: Path) -> bool:
    print(f"\n  RCA TEST  {path.stem}")

    alert = _parse_alert_md(path)

    state = run_investigation(
        alert_name=alert["title"],
        pipeline_name=alert["pipeline_name"],
        severity=alert["severity"],
        raw_alert=alert["raw_alert"],
    )

    passed = bool(state.get("root_cause"))
    category = state.get("root_cause_category") or "—"
    mark = "\033[1;32m●\033[0m" if passed else "\033[1;31m●\033[0m"
    status = "pass" if passed else "fail"
    print(f"\n  {mark}  {status}  {path.stem}  {category}")
    return passed


def main() -> None:
    if len(sys.argv) > 1:
        name = sys.argv[1]
        if not name.endswith(".md"):
            name += ".md"
        targets = [RCA_DIR / name]
    else:
        targets = sorted(RCA_DIR.glob("*.md"))

    if not targets:
        print("No markdown alert files found in tests/rca/")
        sys.exit(1)

    results = [run_file(p) for p in targets]

    total, passed = len(results), sum(results)
    mark = "\033[1;32m●\033[0m" if passed == total else "\033[1;31m●\033[0m"
    print(f"\n  {mark}  {passed}/{total} passed\n")
    if not all(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
