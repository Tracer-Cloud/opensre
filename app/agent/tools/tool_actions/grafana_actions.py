"""Grafana Cloud investigation actions for querying logs, traces, and metrics.

These actions are OPTIONAL and dynamically selected based on service map connectivity.
"""

from __future__ import annotations

try:
    from langchain.tools import tool
except ImportError:

    def tool(func=None, **kwargs):  # type: ignore[no-redef]
        if func is None:
            return lambda f: f
        return func


from app.agent.tools.clients.grafana_client import get_grafana_client


# Service name mapping: Pipeline name -> Grafana service name
SERVICE_NAME_MAPPING = {
    "upstream_downstream_pipeline_lambda_ingester": "lambda-api-ingester",
    "upstream_downstream_pipeline_lambda_mock_dag": "lambda-mock-dag",
    "upstream_downstream_pipeline_prefect": "prefect-etl-pipeline",
    "upstream_downstream_pipeline_airflow": "airflow-etl-pipeline",
    "upstream_downstream_pipeline_flink": "flink-etl-pipeline",
}


def _map_pipeline_to_service_name(pipeline_name: str) -> str:
    """Map Tracer pipeline name to Grafana service name."""
    return SERVICE_NAME_MAPPING.get(pipeline_name, pipeline_name)


def query_grafana_logs(
    service_name: str,
    execution_run_id: str | None = None,
    time_range_minutes: int = 60,
    limit: int = 100,
) -> dict:
    """Query Grafana Cloud Loki for pipeline logs.

    Useful for:
    - Finding error logs from pipeline execution with execution_run_id
    - Validating pipeline completed specific stages (validate_data, transform_data)
    - Checking structured JSON logs for detailed error context

    Args:
        service_name: Grafana service name (e.g., lambda-mock-dag, prefect-etl-pipeline)
        execution_run_id: Optional execution run ID to filter logs
        time_range_minutes: Time range to query in minutes (default 60)
        limit: Maximum logs to return (default 100)

    Returns:
        Dictionary with logs list, metadata, and query success status
    """
    client = get_grafana_client()

    query = f'{{service_name="{service_name}"}}'
    if execution_run_id:
        query = f'{{service_name="{service_name}"}} |= "{execution_run_id}"'

    result = client.query_loki(query, time_range_minutes=time_range_minutes, limit=limit)

    if not result.get("success"):
        return {
            "source": "grafana_loki",
            "available": False,
            "error": result.get("error", "Unknown error"),
            "logs": [],
        }

    logs = result.get("logs", [])

    error_logs = [log for log in logs if "error" in log["message"].lower() or "fail" in log["message"].lower()]

    return {
        "source": "grafana_loki",
        "available": True,
        "logs": logs[:50],
        "error_logs": error_logs[:20],
        "total_logs": result.get("total_logs", 0),
        "service_name": service_name,
        "execution_run_id": execution_run_id,
        "query": query,
    }


def query_grafana_traces(
    service_name: str,
    execution_run_id: str | None = None,
    limit: int = 20,
) -> dict:
    """Query Grafana Cloud Tempo for pipeline traces.

    Useful for:
    - Checking which pipeline stages executed (extract_data, validate_data, transform_data, load_data)
    - Finding execution.run_id for cross-referencing with logs
    - Validating pipeline span duration and identifying bottlenecks
    - Understanding which stage failed (span present vs absent)

    Args:
        service_name: Grafana service name
        execution_run_id: Optional execution run ID to filter traces
        limit: Maximum traces to return (default 20)

    Returns:
        Dictionary with traces and span details
    """
    client = get_grafana_client()

    result = client.query_tempo(service_name, limit=limit)

    if not result.get("success"):
        return {
            "source": "grafana_tempo",
            "available": False,
            "error": result.get("error", "Unknown error"),
            "traces": [],
        }

    traces = result.get("traces", [])

    if execution_run_id and traces:
        filtered_traces = []
        for trace in traces:
            has_execution_run_id = any(
                span.get("attributes", {}).get("execution.run_id") == execution_run_id
                for span in trace.get("spans", [])
            )
            if has_execution_run_id:
                filtered_traces.append(trace)

        traces = filtered_traces if filtered_traces else traces

    pipeline_spans = []
    for trace in traces:
        for span in trace.get("spans", []):
            span_name = span.get("name", "")
            if span_name in ["extract_data", "validate_data", "transform_data", "load_data"]:
                pipeline_spans.append(
                    {
                        "span_name": span_name,
                        "execution_run_id": span.get("attributes", {}).get("execution.run_id"),
                        "record_count": span.get("attributes", {}).get("record_count"),
                    }
                )

    return {
        "source": "grafana_tempo",
        "available": True,
        "traces": traces[:5],
        "pipeline_spans": pipeline_spans,
        "total_traces": result.get("total_traces", 0),
        "service_name": service_name,
        "execution_run_id": execution_run_id,
    }


def query_grafana_metrics(
    metric_name: str,
    service_name: str | None = None,
    execution_run_id: str | None = None,
) -> dict:
    """Query Grafana Cloud Mimir for pipeline metrics.

    Useful for:
    - Checking pipeline_runs_total for execution count
    - Finding records_processed_total for throughput validation
    - Checking pipeline_runs_failed_total for failure rate

    Args:
        metric_name: Prometheus metric name (e.g., pipeline_runs_total, duration_seconds)
        service_name: Optional service filter
        execution_run_id: Optional execution run ID filter (not commonly used in metrics)

    Returns:
        Dictionary with metric series and values
    """
    client = get_grafana_client()

    result = client.query_mimir(metric_name, service_name=service_name)

    if not result.get("success"):
        return {
            "source": "grafana_mimir",
            "available": False,
            "error": result.get("error", "Unknown error"),
            "metrics": [],
        }

    return {
        "source": "grafana_mimir",
        "available": True,
        "metrics": result.get("metrics", []),
        "total_series": result.get("total_series", 0),
        "metric_name": metric_name,
        "service_name": service_name,
    }


def check_grafana_connection(pipeline_name: str) -> dict:
    """Check if pipeline has Grafana datasource connection via service map.

    Useful for:
    - Determining if Grafana actions are available for this pipeline
    - Validating OTLP configuration before querying
    - Understanding observability coverage gaps

    Args:
        pipeline_name: Pipeline name from alert

    Returns:
        Dictionary with connection status and Grafana service name mapping
    """
    from app.agent.memory.service_map import load_service_map

    service_map = load_service_map()

    if not service_map or not service_map.get("enabled"):
        return {
            "connected": False,
            "reason": "Service map not enabled",
            "service_name": None,
        }

    pipeline_asset_id = f"pipeline:{pipeline_name}"

    has_grafana_edge = False
    for edge in service_map.get("edges", []):
        if edge.get("from_asset") == pipeline_asset_id and "grafana" in edge.get("to_asset", ""):
            has_grafana_edge = True
            break

    grafana_service_name = _map_pipeline_to_service_name(pipeline_name)

    return {
        "connected": has_grafana_edge,
        "service_name": grafana_service_name,
        "pipeline_name": pipeline_name,
        "reason": "Service map shows Grafana edge" if has_grafana_edge else "No Grafana edge in service map",
    }


query_grafana_logs_tool = tool(query_grafana_logs)
query_grafana_traces_tool = tool(query_grafana_traces)
query_grafana_metrics_tool = tool(query_grafana_metrics)
check_grafana_connection_tool = tool(check_grafana_connection)
