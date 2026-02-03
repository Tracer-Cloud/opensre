#!/usr/bin/env python3
"""Validate that pipeline spans (validate_data, transform_data, etc.) appear in Grafana Tempo.

This script:
1. Queries Tempo directly for recent traces
2. Checks for required span names: extract_data, validate_data, transform_data, load_data
3. Validates execution.run_id is present in spans
4. Reports which spans are found and which are missing
"""

import json
import sys
import time
from typing import Any

import requests

TEMPO_URL = "http://localhost:3200"


def query_tempo_recent_traces(limit: int = 100) -> list[dict[str, Any]]:
    """Query Tempo for recent traces."""
    try:
        # Tempo search API - query for all traces in the last hour
        response = requests.get(
            f"{TEMPO_URL}/api/search",
            params={
                "limit": limit,
                "start": int((time.time() - 3600) * 1000000000),  # 1 hour ago in nanoseconds
                "end": int(time.time() * 1000000000),  # now in nanoseconds
            },
            timeout=10,
        )
        if response.status_code == 200:
            return response.json().get("traces", [])
    except requests.RequestException as e:
        print(f"Tempo query failed: {e}")
        return []
    return []


def get_trace_details(trace_id: str) -> dict[str, Any] | None:
    """Get detailed trace information from Tempo."""
    try:
        response = requests.get(
            f"{TEMPO_URL}/api/traces/{trace_id}",
            timeout=10,
        )
        if response.status_code == 200:
            return response.json()
    except requests.RequestException as e:
        print(f"Failed to get trace details for {trace_id}: {e}")
    return None


def extract_span_names(trace: dict[str, Any]) -> set[str]:
    """Extract span names from a trace."""
    span_names = set()
    if "batches" in trace:
        for batch in trace["batches"]:
            if "instrumentationLibrarySpans" in batch:
                for ils in batch["instrumentationLibrarySpans"]:
                    if "spans" in ils:
                        for span in ils["spans"]:
                            if "name" in span:
                                span_names.add(span["name"])
    return span_names


def extract_span_attributes(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract span attributes from a trace."""
    spans_with_attrs = []
    if "batches" in trace:
        for batch in trace["batches"]:
            if "instrumentationLibrarySpans" in batch:
                for ils in batch["instrumentationLibrarySpans"]:
                    if "spans" in ils:
                        for span in ils["spans"]:
                            span_info = {
                                "name": span.get("name", "unknown"),
                                "attributes": {},
                            }
                            if "attributes" in span:
                                for attr in span["attributes"]:
                                    if "key" in attr and "value" in attr:
                                        span_info["attributes"][attr["key"]] = attr["value"].get(
                                            "stringValue", ""
                                        )
                            spans_with_attrs.append(span_info)
    return spans_with_attrs


def validate_spans() -> bool:
    """Validate that required spans are present in Tempo."""
    print("Querying Tempo for recent traces...")
    traces = query_tempo_recent_traces(limit=50)
    
    if not traces:
        print("⚠ No traces found in Tempo. Run a pipeline first to generate traces.")
        return False
    
    print(f"Found {len(traces)} traces")
    
    required_spans = {"extract_data", "validate_data", "transform_data", "load_data"}
    found_spans: set[str] = set()
    spans_with_execution_run_id = 0
    total_spans = 0
    
    print("\nAnalyzing traces...")
    for trace_summary in traces[:10]:  # Check first 10 traces
        trace_id = trace_summary.get("traceID", "")
        if not trace_id:
            continue
        
        trace_details = get_trace_details(trace_id)
        if not trace_details:
            continue
        
        span_names = extract_span_names(trace_details)
        found_spans.update(span_names)
        total_spans += len(span_names)
        
        spans_with_attrs = extract_span_attributes(trace_details)
        for span in spans_with_attrs:
            if "execution.run_id" in span["attributes"]:
                spans_with_execution_run_id += 1
    
    print(f"\n{'='*60}")
    print("SPAN VALIDATION REPORT")
    print(f"{'='*60}")
    print(f"\nTotal spans found: {total_spans}")
    print(f"Spans with execution.run_id: {spans_with_execution_run_id}")
    print(f"\nRequired spans:")
    
    all_found = True
    for span_name in sorted(required_spans):
        found = span_name in found_spans
        status = "✓" if found else "✗"
        print(f"  {status} {span_name}")
        if not found:
            all_found = False
    
    print(f"\nAll found spans:")
    for span_name in sorted(found_spans):
        print(f"  - {span_name}")
    
    print(f"\n{'='*60}")
    if all_found and spans_with_execution_run_id > 0:
        print("✓ VALIDATION PASSED")
        print(f"  All required spans present")
        print(f"  execution.run_id found in {spans_with_execution_run_id} spans")
    else:
        print("✗ VALIDATION FAILED")
        if not all_found:
            missing = required_spans - found_spans
            print(f"  Missing spans: {', '.join(missing)}")
        if spans_with_execution_run_id == 0:
            print(f"  No spans found with execution.run_id attribute")
    print(f"{'='*60}")
    
    return all_found and spans_with_execution_run_id > 0


def main() -> int:
    """Main entry point."""
    print("Validating pipeline spans in Grafana Tempo...")
    print(f"Tempo URL: {TEMPO_URL}")
    print()
    
    try:
        # Check if Tempo is accessible by trying to query it
        response = requests.get(f"{TEMPO_URL}/api/search", params={"limit": 1}, timeout=5)
        # 200 or 404 (no traces) are both OK - means Tempo is running
        if response.status_code not in (200, 404):
            print(f"⚠ Tempo returned unexpected status: {response.status_code}")
            print("Make sure the local Grafana stack is running:")
            print("  cd tests/observability && docker compose up -d")
            return 1
    except requests.RequestException as e:
        print(f"⚠ Cannot connect to Tempo at {TEMPO_URL}")
        print(f"Error: {e}")
        print("\nMake sure the local Grafana stack is running:")
        print("  cd tests/observability && docker compose up -d")
        return 1
    
    success = validate_spans()
    
    if not success:
        print("\n💡 To generate traces, run a pipeline locally:")
        print("  export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317")
        print("  export OTEL_EXPORTER_OTLP_PROTOCOL=grpc")
        print("  python tests/test_case_upstream_prefect_ecs_fargate/test_local_grafana.py")
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
