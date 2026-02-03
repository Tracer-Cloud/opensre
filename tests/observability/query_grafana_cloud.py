#!/usr/bin/env python3
"""Query Grafana Cloud Loki and Tempo to validate telemetry.

Requires a READ token. Get one from:
https://grafana.com/docs/grafana-cloud/account-management/authentication-and-permissions/access-policies/
"""

import argparse
import json
import os
import sys
from typing import Any

import requests


def query_loki(
    grafana_instance: str,
    read_token: str,
    query: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Query Grafana Cloud Loki via Grafana instance API."""
    url = f"{grafana_instance}/api/datasources/proxy/uid/grafanacloud-logs/loki/api/v1/query_range"
    
    headers = {"Authorization": f"Bearer {read_token}"}
    
    import time
    
    # Use Unix timestamps in nanoseconds
    end_ns = int(time.time() * 1e9)
    start_ns = end_ns - (3600 * int(1e9))  # 1 hour ago
    
    params = {
        "query": query,
        "limit": limit,
        "start": str(start_ns),
        "end": str(end_ns),
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            return data.get("data", {}).get("result", [])
        else:
            print(f"Loki query failed: {response.status_code}")
            print(f"Response: {response.text[:300]}")
            return []
    except Exception as e:
        print(f"Loki query error: {e}")
        return []


def query_tempo(
    grafana_instance: str,
    read_token: str,
    service_name: str,
) -> list[dict[str, Any]]:
    """Query Grafana Cloud Tempo via Grafana instance API."""
    url = f"{grafana_instance}/api/datasources/proxy/uid/grafanacloud-traces/api/search"
    
    headers = {"Authorization": f"Bearer {read_token}"}
    
    params = {
        "q": f'{{.service.name="{service_name}"}}',
        "limit": 20,
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            return data.get("traces", [])
        else:
            print(f"Tempo query failed: {response.status_code}")
            print(f"Response: {response.text[:300]}")
            return []
    except Exception as e:
        print(f"Tempo query error: {e}")
        return []


def validate_spans_in_trace(
    grafana_instance: str,
    read_token: str,
    trace_id: str,
) -> dict[str, Any]:
    """Get trace details and extract span names."""
    url = f"{grafana_instance}/api/datasources/proxy/uid/grafanacloud-traces/api/traces/{trace_id}"
    
    headers = {"Authorization": f"Bearer {read_token}"}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            trace_data = response.json()
            span_names = set()
            
            # Extract span names from trace
            if "batches" in trace_data:
                for batch in trace_data["batches"]:
                    if "scopeSpans" in batch:
                        for scope in batch["scopeSpans"]:
                            if "spans" in scope:
                                for span in scope["spans"]:
                                    span_names.add(span.get("name", "unknown"))
            
            return {
                "trace_id": trace_id,
                "span_names": list(span_names),
                "span_count": len(span_names),
            }
    except Exception as e:
        print(f"Trace detail query error: {e}")
    
    return {"trace_id": trace_id, "span_names": [], "span_count": 0}


def main():
    parser = argparse.ArgumentParser(description="Query Grafana Cloud for telemetry")
    parser.add_argument("--token", required=True, help="Grafana Cloud READ token")
    parser.add_argument("--instance", default="https://tracerbio.grafana.net", help="Grafana instance URL")
    parser.add_argument("--service", help="Filter by service name")
    parser.add_argument("--execution-run-id", help="Filter by execution run ID")
    args = parser.parse_args()
    
    print("=" * 80)
    print("GRAFANA CLOUD TELEMETRY VALIDATION")
    print("=" * 80)
    print()
    
    # Query Loki for logs
    print("=== LOKI LOGS ===")
    if args.execution_run_id:
        log_query = f'{{service_name=~".*"}} |= "{args.execution_run_id}"'
    elif args.service:
        log_query = f'{{service_name="{args.service}"}}'
    else:
        log_query = '{service_name=~".*etl.*|.*ingester.*|.*dag.*|.*test.*"}'
    
    print(f"Query: {log_query}")
    
    log_results = query_loki(args.instance, args.token, log_query)
    
    if log_results:
        total_logs = sum(len(r.get("values", [])) for r in log_results)
        print(f"✓ Found {len(log_results)} log streams")
        print(f"✓ Total log entries: {total_logs}")
        
        print("\nLog streams:")
        for r in log_results[:10]:
            stream = r.get("stream", {})
            values = r.get("values", [])
            print(f"  - {stream.get('service_name', 'unknown')}: {len(values)} entries")
            if values and args.execution_run_id:
                # Show sample log containing execution_run_id
                for val in values[:2]:
                    if args.execution_run_id in val[1]:
                        print(f"    Sample: {val[1][:150]}...")
                        break
    else:
        print("✗ NO LOGS FOUND")
    
    print()
    
    # Query Tempo for traces
    print("=== TEMPO TRACES ===")
    if args.service:
        service_filter = args.service
    else:
        service_filter = "test-s3-logs-only"
    
    print(f"Service: {service_filter}")
    
    trace_results = query_tempo(args.instance, args.token, service_filter)
    
    if trace_results:
        print(f"✓ Found {len(trace_results)} traces")
        
        print("\nTraces:")
        required_spans = {"extract_data", "validate_data", "transform_data", "load_data"}
        found_spans = set()
        
        for trace_summary in trace_results[:5]:
            trace_id = trace_summary.get("traceID", "")
            root_service = trace_summary.get("rootServiceName", "unknown")
            span_count = trace_summary.get("spanCount", 0)
            
            print(f"  - Trace {trace_id[:16]}: {root_service} ({span_count} spans)")
            
            # Get detailed span info
            details = validate_spans_in_trace(args.instance, args.token, trace_id)
            if details["span_names"]:
                print(f"    Spans: {', '.join(details['span_names'])}")
                found_spans.update(details["span_names"])
        
        print(f"\nAll found span names: {', '.join(sorted(found_spans))}")
        
        missing_spans = required_spans - found_spans
        if missing_spans:
            print(f"⚠ Missing expected spans: {', '.join(missing_spans)}")
        else:
            print(f"✓ All expected spans present: {', '.join(sorted(required_spans))}")
    else:
        print("✗ NO TRACES FOUND")
    
    print()
    print("=" * 80)
    
    if log_results and trace_results:
        print("✓ VALIDATION PASSED - Logs and traces found in Grafana Cloud")
        return 0
    elif log_results:
        print("⚠ PARTIAL - Logs found but no traces")
        return 1
    elif trace_results:
        print("⚠ PARTIAL - Traces found but no logs")
        return 1
    else:
        print("✗ VALIDATION FAILED - No logs or traces found")
        return 1


if __name__ == "__main__":
    sys.exit(main())
