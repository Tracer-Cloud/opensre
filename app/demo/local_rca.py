"""Run a bundled local RCA demo with sample alert and evidence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv

load_dotenv(override=False)

from app.agent.nodes.publish_findings.node import generate_report  # noqa: E402
from app.agent.nodes.root_cause_diagnosis.node import diagnose_root_cause  # noqa: E402
from app.agent.state import InvestigationState, make_initial_state  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE_PATH = (
    REPO_ROOT / "tests" / "test_case_kubernetes" / "fixtures" / "datadog_k8s_alert.json"
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a bundled local RCA example and render the report."
    )
    parser.add_argument(
        "--fixture",
        default=str(DEFAULT_FIXTURE_PATH),
        help="Path to a bundled alert+evidence fixture JSON file.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to write the rendered RCA report as Markdown.",
    )
    return parser.parse_args(argv)


def load_demo_fixture(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected fixture object in {path}")

    alert = payload.get("alert")
    evidence = payload.get("evidence")
    if not isinstance(alert, dict) or not isinstance(evidence, dict):
        raise ValueError(f"Fixture {path} must contain 'alert' and 'evidence' objects")

    return payload


def prepare_demo_state(fixture: dict[str, Any]) -> InvestigationState:
    alert = cast(dict[str, Any], fixture["alert"])
    evidence = cast(dict[str, Any], fixture["evidence"])
    meta = cast(dict[str, Any], fixture.get("_meta", {}))

    common_labels = cast(dict[str, Any], alert.get("commonLabels", {}))
    common_annotations = cast(dict[str, Any], alert.get("commonAnnotations", {}))

    alert_name = str(alert.get("title") or common_labels.get("alertname") or "Bundled RCA Demo")
    pipeline_name = str(common_labels.get("pipeline_name") or common_labels.get("table") or "unknown")
    severity = str(common_labels.get("severity") or "critical")
    kube_namespace = str(common_annotations.get("kube_namespace") or "")
    error_message = str(common_annotations.get("summary") or "")

    state = make_initial_state(
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
        raw_alert={
            **alert,
            "alert_source": "datadog",
            "kube_namespace": kube_namespace,
            "error_message": error_message,
        },
    )
    state["problem_md"] = build_problem_md(
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
        kube_namespace=kube_namespace,
        error_message=error_message,
    )
    state["alert_source"] = "datadog"
    state["evidence"] = evidence
    state["available_sources"] = {
        "datadog": {"site": str(meta.get("datadog_site") or "datadoghq.com")}
    }
    return state


def build_problem_md(
    *,
    alert_name: str,
    pipeline_name: str,
    severity: str,
    kube_namespace: str,
    error_message: str,
) -> str:
    parts = [f"# {alert_name}", f"Pipeline: {pipeline_name} | Severity: {severity}"]
    if kube_namespace:
        parts.append(f"Namespace: {kube_namespace}")
    if error_message:
        parts.append(f"\nError: {error_message}")
    return "\n".join(parts)


def require_llm_config(rerun_command: str = "make local-rca-demo") -> None:
    provider = (os.getenv("LLM_PROVIDER") or "anthropic").strip().lower()
    if provider == "openai":
        if not (os.getenv("OPENAI_API_KEY") or "").strip():
            raise SystemExit(
                "Missing OPENAI_API_KEY. Set it in your environment or .env, then rerun "
                f"`{rerun_command}`."
            )
        return

    if not (os.getenv("ANTHROPIC_API_KEY") or "").strip():
        raise SystemExit(
            "Missing ANTHROPIC_API_KEY. Set it in your environment or .env, then rerun "
            f"`{rerun_command}`."
        )


def run_demo(argv: list[str] | None = None) -> str:
    args = parse_args(argv)
    require_llm_config("make local-rca-demo")

    fixture = load_demo_fixture(Path(args.fixture))
    state = prepare_demo_state(fixture)

    diagnosis = cast(InvestigationState, diagnose_root_cause(state))
    state.update(diagnosis)

    report = str(generate_report(state)["slack_message"])
    if args.output:
        Path(args.output).write_text(report + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    run_demo(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
