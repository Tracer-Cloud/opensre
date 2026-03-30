"""Run RCA against a real local Grafana instance with synthetic alert payload."""

from __future__ import annotations

import argparse
import time
from typing import Any, cast

import requests
from dotenv import load_dotenv

load_dotenv(override=False)

from app.agent.nodes.publish_findings.node import generate_report  # noqa: E402
from app.agent.nodes.root_cause_diagnosis.node import diagnose_root_cause  # noqa: E402
from app.agent.state import InvestigationState, make_initial_state  # noqa: E402
from app.agent.tools.clients.grafana import get_grafana_client_from_credentials  # noqa: E402
from app.agent.tools.tool_actions.grafana.grafana_actions import (  # noqa: E402
    query_grafana_logs,
    query_grafana_service_names,
)
from app.demo.local_grafana_seed import (  # noqa: E402
    DEMO_CORRELATION_ID,
    DEMO_RUN_ID,
    PIPELINE_NAME,
    SERVICE_NAME,
)
from app.demo.local_rca import require_llm_config  # noqa: E402

LOCAL_GRAFANA_URL = "http://localhost:3000"
DEMO_TIME_RANGE_MINUTES = 15


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run RCA against a live local Grafana+Loki demo stack."
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to write the rendered RCA report as Markdown.",
    )
    return parser.parse_args(argv)


def ensure_local_grafana_running() -> None:
    try:
        response = requests.get(f"{LOCAL_GRAFANA_URL}/api/health", timeout=3)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SystemExit(
            "Local Grafana is not running. Start it with `make grafana-local-up` and retry."
        ) from exc


def inspect_local_grafana() -> dict[str, Any]:
    """Verify that local Grafana is reachable and its Loki datasource is queryable."""
    client = get_grafana_client_from_credentials(
        endpoint=LOCAL_GRAFANA_URL,
        api_key="",
        account_id="local_grafana_demo",
    )
    loki_uid = client.loki_datasource_uid
    if not loki_uid:
        raise SystemExit(
            "Local Grafana is reachable, but Tracer could not discover a Loki datasource "
            "through Grafana `/api/datasources`."
        )

    services_result = query_grafana_service_names(
        grafana_endpoint=LOCAL_GRAFANA_URL,
        grafana_api_key="",
    )
    if not services_result.get("available"):
        raise SystemExit(
            "Tracer reached local Grafana, but could not query Loki label values via Grafana."
        )

    service_names = services_result.get("service_names", [])
    return {
        "grafana_endpoint": LOCAL_GRAFANA_URL,
        "grafana_loki_datasource_uid": loki_uid,
        "grafana_service_names": service_names,
        "grafana_query_transport": "grafana_datasource_proxy",
    }


def fetch_live_grafana_evidence() -> dict[str, Any]:
    last_error = "unknown"
    last_connection: dict[str, Any] = {}
    for _ in range(10):
        last_connection = inspect_local_grafana()
        result = query_grafana_logs(
            SERVICE_NAME,
            execution_run_id=DEMO_RUN_ID,
            time_range_minutes=DEMO_TIME_RANGE_MINUTES,
            limit=100,
            grafana_endpoint=LOCAL_GRAFANA_URL,
            grafana_api_key="",
        )
        if result.get("available") and result.get("logs"):
            print(
                "[local-grafana-live] Connected to local Grafana via datasource "
                f"{last_connection['grafana_loki_datasource_uid']} and fetched "
                f"{len(result.get('logs', []))} live logs for run {DEMO_RUN_ID}."
            )
            return {
                **last_connection,
                "grafana_logs": result.get("logs", []),
                "grafana_error_logs": result.get("error_logs", []),
                "grafana_logs_query": result.get("query", ""),
                "grafana_logs_service": result.get("service_name", ""),
                "grafana_demo_run_id": DEMO_RUN_ID,
                "grafana_demo_correlation_id": DEMO_CORRELATION_ID,
            }
        last_error = str(result.get("error") or "no logs found")
        time.sleep(1)

    raise SystemExit(
        "Tracer could not query the local Grafana stack yet. "
        f"Last error: {last_error}. "
        f"Last datasource info: {last_connection}. "
        "Make sure `make grafana-local-up` completed, then retry `make local-grafana-live`."
    )


def build_synthetic_alert() -> dict[str, Any]:
    return {
        "title": "[FIRING:1] LocalGrafanaPipelineFailure critical - events_fact",
        "state": "alerting",
        "commonLabels": {
            "alertname": "LocalGrafanaPipelineFailure",
            "severity": "critical",
            "pipeline_name": PIPELINE_NAME,
            "grafana_folder": "local-demos",
        },
        "commonAnnotations": {
            "summary": "events_fact stopped updating after a local pipeline failure",
            "source_url": f"{LOCAL_GRAFANA_URL}/explore",
            "execution_run_id": DEMO_RUN_ID,
            "correlation_id": DEMO_CORRELATION_ID,
            "log_query": f'{{service_name="{SERVICE_NAME}"}} |= "{DEMO_RUN_ID}"',
        },
        "externalURL": LOCAL_GRAFANA_URL,
        "message": (
            "Synthetic local Grafana alert for the live demo. "
            "The events_fact pipeline stopped updating after an authentication failure."
        ),
    }


def build_problem_md(
    *,
    alert_name: str,
    pipeline_name: str,
    severity: str,
    error_message: str,
) -> str:
    parts = [f"# {alert_name}", f"Pipeline: {pipeline_name} | Severity: {severity}"]
    if error_message:
        parts.append(f"\nError: {error_message}")
    return "\n".join(parts)


def prepare_demo_state(evidence: dict[str, Any]) -> InvestigationState:
    alert = build_synthetic_alert()
    state = make_initial_state(
        alert_name=str(alert["title"]),
        pipeline_name=PIPELINE_NAME,
        severity="critical",
        raw_alert={**alert, "alert_source": "grafana"},
    )
    state["problem_md"] = build_problem_md(
        alert_name=str(alert["title"]),
        pipeline_name=PIPELINE_NAME,
        severity="critical",
        error_message=str(alert["commonAnnotations"]["summary"]),
    )
    state["alert_source"] = "grafana"
    state["evidence"] = evidence
    state["available_sources"] = {
        "grafana": {
            "connection_verified": True,
            "grafana_endpoint": LOCAL_GRAFANA_URL,
            "service_name": SERVICE_NAME,
            "execution_run_id": DEMO_RUN_ID,
            "time_range_minutes": DEMO_TIME_RANGE_MINUTES,
        }
    }
    return state


def run_demo(argv: list[str] | None = None) -> str:
    args = parse_args(argv)
    require_llm_config("make local-grafana-live")
    ensure_local_grafana_running()

    evidence = fetch_live_grafana_evidence()
    state = prepare_demo_state(evidence)

    diagnosis = cast(InvestigationState, diagnose_root_cause(state))
    state.update(diagnosis)

    report = str(generate_report(state)["slack_message"])
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(report + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    run_demo(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
