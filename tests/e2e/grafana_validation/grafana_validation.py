"""Grafana Cloud telemetry validation utilities.

Validates that telemetry (logs, traces, metrics) appears in Grafana Cloud
after pipeline execution.

Usage:
    from tests.e2e.grafana_validation.grafana_validation import validate_grafana_telemetry

    result = validate_grafana_telemetry(
        service_name="prefect-etl-pipeline",
        execution_run_id="abc-123",
        wait_seconds=10,
    )
    print(f"Logs found: {result['logs_found']}")
    print(f"Traces found: {result['traces_found']}")
"""

import sys
import time


def validate_grafana_telemetry(
    service_name: str,
    execution_run_id: str | None = None,
    wait_seconds: int = 5,
    expected_spans: set[str] | None = None,
) -> dict:
    """Validate telemetry appears in Grafana Cloud.

    Args:
        service_name: Grafana service name (e.g., "prefect-etl-pipeline")
        execution_run_id: Optional execution run ID to filter by
        wait_seconds: Seconds to wait for telemetry to propagate
        expected_spans: Optional set of expected span names to check

    Returns:
        dict with validation results:
        - logs_found: bool
        - traces_found: bool
        - total_logs: int
        - total_traces: int
        - pipeline_spans: list of span names found
        - all_expected_spans_found: bool (if expected_spans provided)
        - missing_spans: list (if expected_spans provided)
        - passed: bool (overall validation result)
    """
    from app.tools.GrafanaLogsTool import query_grafana_logs
    from app.tools.GrafanaTracesTool import query_grafana_traces

    if wait_seconds > 0:
        print(f"Waiting {wait_seconds}s for telemetry to propagate to Grafana Cloud...")
        time.sleep(wait_seconds)

    result = {
        "service_name": service_name,
        "execution_run_id": execution_run_id,
        "logs_found": False,
        "traces_found": False,
        "total_logs": 0,
        "total_traces": 0,
        "pipeline_spans": [],
        "error_logs": [],
        "passed": False,
    }

    # Query logs
    logs_result = query_grafana_logs(
        service_name=service_name,
        execution_run_id=execution_run_id,
        time_range_minutes=30,
        limit=100,
    )

    if logs_result.get("available"):
        result["total_logs"] = logs_result.get("total_logs", 0)
        result["logs_found"] = result["total_logs"] > 0
        result["error_logs"] = [
            log.get("message", "")[:200]
            for log in logs_result.get("error_logs", [])[:5]
        ]

    # Query traces
    traces_result = query_grafana_traces(
        service_name=service_name,
        execution_run_id=execution_run_id,
        limit=20,
    )

    if traces_result.get("available"):
        result["total_traces"] = traces_result.get("total_traces", 0)
        result["traces_found"] = result["total_traces"] > 0
        result["pipeline_spans"] = [
            span.get("span_name")
            for span in traces_result.get("pipeline_spans", [])
        ]

    # Check expected spans if provided
    if expected_spans:
        found_spans = set(result["pipeline_spans"])
        result["all_expected_spans_found"] = expected_spans.issubset(found_spans)
        result["missing_spans"] = list(expected_spans - found_spans)

    # Overall pass/fail
    result["passed"] = result["logs_found"] or result["traces_found"]

    return result


def print_grafana_validation_report(result: dict) -> None:
    """Print a formatted validation report."""
    print("\n" + "=" * 60)
    print("GRAFANA CLOUD VALIDATION")
    print("=" * 60)
    print(f"Service: {result['service_name']}")
    if result.get("execution_run_id"):
        print(f"Execution Run ID: {result['execution_run_id']}")

    print("\nLogs:")
    print(f"  Found: {'YES' if result['logs_found'] else 'NO'}")
    print(f"  Total: {result['total_logs']}")
    if result.get("error_logs"):
        print(f"  Error logs ({len(result['error_logs'])}):")
        for log in result["error_logs"][:3]:
            print(f"    - {log[:80]}...")

    print("\nTraces:")
    print(f"  Found: {'YES' if result['traces_found'] else 'NO'}")
    print(f"  Total: {result['total_traces']}")
    if result.get("pipeline_spans"):
        unique_spans = sorted(set(result["pipeline_spans"]))
        print(f"  Pipeline spans: {', '.join(unique_spans)}")

    if "all_expected_spans_found" in result:
        print("\nExpected Spans Check:")
        if result["all_expected_spans_found"]:
            print("  All expected spans found")
        else:
            print(f"  MISSING: {', '.join(result['missing_spans'])}")

    print("\n" + "-" * 60)
    if result["passed"]:
        print("VALIDATION PASSED - Telemetry found in Grafana Cloud")
    else:
        print("VALIDATION FAILED - No telemetry found in Grafana Cloud")
    print("=" * 60)


# Convenience function combining validate + print
def validate_and_report(
    service_name: str,
    execution_run_id: str | None = None,
    wait_seconds: int = 5,
    expected_spans: set[str] | None = None,
) -> bool:
    """Validate telemetry and print report.

    Returns:
        True if validation passed, False otherwise
    """
    result = validate_grafana_telemetry(
        service_name=service_name,
        execution_run_id=execution_run_id,
        wait_seconds=wait_seconds,
        expected_spans=expected_spans,
    )
    print_grafana_validation_report(result)
    return result["passed"]


if __name__ == "__main__":
    from pathlib import Path

    from app.utils.config import load_env

    load_env(Path(__file__).resolve().parent.parent.parent / ".env")

    import argparse

    parser = argparse.ArgumentParser(description="Validate Grafana Cloud telemetry")
    parser.add_argument("--service", default="prefect-etl-pipeline", help="Service name")
    parser.add_argument("--run-id", help="Execution run ID to filter")
    parser.add_argument("--wait", type=int, default=0, help="Seconds to wait")
    args = parser.parse_args()

    passed = validate_and_report(
        service_name=args.service,
        execution_run_id=args.run_id,
        wait_seconds=args.wait,
    )
    sys.exit(0 if passed else 1)
