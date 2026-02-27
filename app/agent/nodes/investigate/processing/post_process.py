"""Post-processing: merge evidence and track hypotheses."""

import json
from collections.abc import Callable
from typing import Any


def _parse_vendor_audit_from_logs(logs: list) -> dict | None:
    """Extract EXTERNAL_API_AUDIT structured logs from Lambda logs."""
    for log_entry in logs:
        message = log_entry.get("message", "") if isinstance(log_entry, dict) else str(log_entry)
        if "EXTERNAL_API_AUDIT:" in message:
            try:
                audit_json = message.split("EXTERNAL_API_AUDIT:", 1)[1].strip()
                result = json.loads(audit_json)
                return result if isinstance(result, dict) else None
            except (json.JSONDecodeError, IndexError):
                continue
    return None


def _map_failed_jobs(data: dict) -> dict:
    return {
        "failed_jobs": data.get("failed_jobs", []),
        "total_jobs": data.get("total_jobs", 0),
    }


def _map_failed_tools(data: dict) -> dict:
    return {
        "failed_tools": data.get("failed_tools", []),
        "total_tools": data.get("total_tools", 0),
    }


def _map_error_logs(data: dict) -> dict:
    return {
        "error_logs": data.get("logs", []),
        "total_logs": data.get("total_logs", 0),
    }


def _map_host_metrics(data: dict) -> dict:
    return {"host_metrics": data.get("metrics", {})}


def _map_cloudwatch_logs(data: dict) -> dict:
    return {
        "cloudwatch_logs": data.get("error_logs", []),
        "cloudwatch_event_count": data.get("event_count", 0),
        "cloudwatch_latest_error": data.get("latest_error"),
    }


def _map_inspect_s3_object(data: dict) -> dict:
    return {
        "s3_object": {
            "bucket": data.get("bucket"),
            "key": data.get("key"),
            "found": data.get("found", False),
            "size": data.get("size"),
            "content_type": data.get("content_type"),
            "metadata": data.get("metadata", {}),
            "sample": data.get("sample"),
            "is_text": data.get("is_text", False),
        }
    }


def _map_list_s3_objects(data: dict) -> dict:
    return {
        "s3_objects": data.get("objects", []),
        "s3_object_count": data.get("count", 0),
    }


def _map_lambda_invocation_logs(data: dict) -> dict:
    result = {
        "lambda_logs": data.get("recent_logs", []),
        "lambda_invocation_count": data.get("invocation_count", 0),
        "lambda_invocations": data.get("invocations", []),
    }
    vendor_audit = _parse_vendor_audit_from_logs(data.get("recent_logs", []))
    if vendor_audit:
        result["vendor_audit_from_logs"] = vendor_audit
    return result


def _map_lambda_errors(data: dict) -> dict:
    return {
        "lambda_errors": data.get("recent_logs", []),
        "lambda_error_count": data.get("invocation_count", 0),
    }


def _map_inspect_lambda_function(data: dict) -> dict:
    return {
        "lambda_function": {
            "function_name": data.get("function_name"),
            "runtime": data.get("runtime"),
            "handler": data.get("handler"),
            "timeout": data.get("timeout"),
            "memory_size": data.get("memory_size"),
            "environment_variables": data.get("environment_variables", {}),
            "code": data.get("code", {}),
        }
    }


def _map_lambda_configuration(data: dict) -> dict:
    return {
        "lambda_config": {
            "function_name": data.get("function_name"),
            "runtime": data.get("runtime"),
            "handler": data.get("handler"),
            "timeout": data.get("timeout"),
            "memory_size": data.get("memory_size"),
            "environment_variables": data.get("environment_variables", {}),
        }
    }


def _map_check_s3_marker(data: dict) -> dict:
    return {
        "s3_marker": {
            "marker_exists": data.get("marker_exists", False),
            "file_count": data.get("file_count", 0),
            "files": data.get("files", []),
        }
    }


def _map_s3_object(data: dict) -> dict:
    return {
        "s3_audit_payload": {
            "bucket": data.get("bucket"),
            "key": data.get("key"),
            "found": data.get("found", False),
            "content": data.get("content"),
            "metadata": data.get("metadata", {}),
        }
    }


def _map_grafana_logs(data: dict) -> dict:
    return {
        "grafana_logs": data.get("logs", []),
        "grafana_error_logs": data.get("error_logs", []),
        "grafana_logs_query": data.get("query", ""),
        "grafana_logs_service": data.get("service_name", ""),
    }


def _map_grafana_traces(data: dict) -> dict:
    return {
        "grafana_traces": data.get("traces", []),
        "grafana_pipeline_spans": data.get("pipeline_spans", []),
        "grafana_traces_service": data.get("service_name", ""),
    }


def _map_grafana_metrics(data: dict) -> dict:
    return {
        "grafana_metrics": data.get("metrics", []),
        "grafana_metric_name": data.get("metric_name", ""),
        "grafana_metrics_service": data.get("service_name", ""),
    }


def _map_grafana_alert_rules(data: dict) -> dict:
    return {
        "grafana_alert_rules": data.get("rules", []),
        "grafana_alert_rules_count": data.get("total_rules", 0),
    }


def _map_grafana_service_names(data: dict) -> dict:
    return {
        "grafana_service_names": data.get("service_names", []),
    }


def _map_datadog_logs(data: dict) -> dict:
    return {
        "datadog_logs": data.get("logs", []),
        "datadog_error_logs": data.get("error_logs", []),
        "datadog_logs_query": data.get("query", ""),
    }


def _map_datadog_monitors(data: dict) -> dict:
    return {
        "datadog_monitors": data.get("monitors", []),
        "datadog_monitors_count": data.get("total", 0),
    }


def _map_datadog_events(data: dict) -> dict:
    return {
        "datadog_events": data.get("events", []),
        "datadog_events_count": data.get("total", 0),
    }


def _map_datadog_all(data: dict) -> dict:
    monitors = data.get("monitors", [])
    events = data.get("events", [])
    return {
        "datadog_logs": data.get("logs", []),
        "datadog_error_logs": data.get("error_logs", []),
        "datadog_logs_query": data.get("query", ""),
        "datadog_monitors": monitors,
        "datadog_monitors_count": len(monitors),
        "datadog_events": events,
        "datadog_events_count": len(events),
        "datadog_fetch_ms": data.get("fetch_duration_ms", {}),
        "datadog_pod_name": data.get("pod_name"),
        "datadog_container_name": data.get("container_name"),
        "datadog_kube_namespace": data.get("kube_namespace"),
    }


EVIDENCE_MAPPERS: dict[str, Callable[[dict], dict]] = {
    "get_failed_jobs": _map_failed_jobs,
    "get_failed_tools": _map_failed_tools,
    "get_error_logs": _map_error_logs,
    "get_host_metrics": _map_host_metrics,
    "get_cloudwatch_logs": _map_cloudwatch_logs,
    "inspect_s3_object": _map_inspect_s3_object,
    "check_s3_marker": _map_check_s3_marker,
    "list_s3_objects": _map_list_s3_objects,
    "get_lambda_invocation_logs": _map_lambda_invocation_logs,
    "get_lambda_errors": _map_lambda_errors,
    "inspect_lambda_function": _map_inspect_lambda_function,
    "get_lambda_configuration": _map_lambda_configuration,
    "get_s3_object": _map_s3_object,
    "query_grafana_logs": _map_grafana_logs,
    "query_grafana_traces": _map_grafana_traces,
    "query_grafana_metrics": _map_grafana_metrics,
    "query_grafana_alert_rules": _map_grafana_alert_rules,
    "query_grafana_service_names": _map_grafana_service_names,
    "query_datadog_logs": _map_datadog_logs,
    "query_datadog_monitors": _map_datadog_monitors,
    "query_datadog_events": _map_datadog_events,
    "query_datadog_all": _map_datadog_all,
}


def merge_evidence(current_evidence: dict[str, Any], execution_results: dict) -> dict[str, Any]:
    """
    Merge execution results into evidence state.

    Args:
        current_evidence: Current evidence dictionary
        execution_results: Results from action execution

    Returns:
        Updated evidence dictionary
    """
    evidence = current_evidence.copy()

    for action_name, result in execution_results.items():
        if not result.success:
            continue

        mapper = EVIDENCE_MAPPERS.get(action_name)
        if mapper:
            evidence.update(mapper(result.data))

    return evidence


def track_hypothesis(
    executed_hypotheses: list[dict[str, Any]],
    action_names: list[str],
    rationale: str,
    investigation_loop_count: int,
) -> list[dict[str, Any]]:
    """
    Track executed hypothesis for deduplication.

    Args:
        executed_hypotheses: Current list of executed hypotheses
        action_names: List of actions that were executed
        rationale: Rationale for executing these actions
        investigation_loop_count: Current loop count

    Returns:
        Updated executed_hypotheses list
    """
    new_hypothesis = {
        "actions": action_names,
        "rationale": rationale,
        "loop_count": investigation_loop_count,
    }
    executed_hypotheses.append(new_hypothesis)
    return executed_hypotheses


def build_evidence_summary(execution_results: dict) -> str:
    """
    Build a summary of what evidence was collected.

    Args:
        execution_results: Results from action execution

    Returns:
        Summary string
    """
    summary_parts = []
    errors = []
    for action_name, result in execution_results.items():
        if result.success:
            data = result.data
            if action_name == "get_failed_jobs" and data.get("failed_jobs"):
                summary_parts.append(f"jobs:{len(data['failed_jobs'])}")
            elif action_name == "get_failed_tools" and data.get("failed_tools"):
                summary_parts.append(f"tools:{len(data['failed_tools'])}")
            elif action_name == "get_error_logs" and data.get("logs"):
                summary_parts.append(f"logs:{len(data['logs'])}")
            elif action_name == "get_cloudwatch_logs" and data.get("error_logs"):
                summary_parts.append(f"cloudwatch:{len(data['error_logs'])} events")
            elif action_name == "inspect_s3_object" and data.get("found"):
                summary_parts.append("s3:object inspected")
            elif action_name == "list_s3_objects" and data.get("objects"):
                summary_parts.append(f"s3:{len(data['objects'])} objects")
            elif action_name == "get_lambda_invocation_logs" and data.get("recent_logs"):
                summary_parts.append(f"lambda:{len(data['recent_logs'])} logs")
            elif action_name == "get_lambda_errors" and data.get("recent_logs"):
                summary_parts.append(f"lambda:{len(data['recent_logs'])} errors")
            elif action_name == "inspect_lambda_function" and data.get("found"):
                summary_parts.append("lambda:function inspected")
            elif action_name == "get_lambda_configuration" and data.get("found"):
                summary_parts.append("lambda:config retrieved")
            elif action_name == "get_s3_object" and data.get("found"):
                summary_parts.append("s3:audit payload retrieved")
            elif action_name == "query_grafana_logs" and data.get("logs"):
                error_count = len(data.get("error_logs", []))
                summary_parts.append(f"grafana:{len(data['logs'])} logs ({error_count} errors)")
            elif action_name == "query_grafana_traces" and data.get("traces"):
                summary_parts.append(f"grafana:{len(data['traces'])} traces")
            elif action_name == "query_grafana_metrics" and data.get("metrics"):
                summary_parts.append(f"grafana:{len(data['metrics'])} metric series")
            elif action_name == "query_grafana_alert_rules" and data.get("rules"):
                summary_parts.append(f"grafana:{len(data['rules'])} alert rules")
            elif action_name == "query_grafana_service_names" and data.get("service_names"):
                summary_parts.append(f"grafana:{len(data['service_names'])} services")
            elif action_name == "query_datadog_logs" and data.get("logs"):
                error_count = len(data.get("error_logs", []))
                summary_parts.append(f"datadog:{len(data['logs'])} logs ({error_count} errors)")
            elif action_name == "query_datadog_monitors" and data.get("monitors"):
                summary_parts.append(f"datadog:{len(data['monitors'])} monitors")
            elif action_name == "query_datadog_events" and data.get("events"):
                summary_parts.append(f"datadog:{len(data['events'])} events")
            elif action_name == "query_datadog_all":
                logs = data.get("logs", [])
                error_logs = data.get("error_logs", [])
                monitors = data.get("monitors", [])
                events = data.get("events", [])
                fetch_ms = data.get("fetch_duration_ms", {})
                max_ms = max(fetch_ms.values()) if fetch_ms else 0
                timing = f" in {max_ms / 1000:.1f}s" if max_ms else ""
                summary_parts.append(
                    f"datadog:{len(logs)} logs ({len(error_logs)} errors), "
                    f"{len(monitors)} monitors, {len(events)} events{timing}"
                )
        else:
            # Log action failures for debugging
            error_msg = f"{action_name}:FAILED({result.error[:50] if result.error else 'unknown'})"
            errors.append(error_msg)
            print(f"[WARNING] Action failed: {error_msg}")

    if errors:
        summary = ", ".join(summary_parts) if summary_parts else "No evidence collected"
        return f"{summary} | Errors: {', '.join(errors)}"

    return ", ".join(summary_parts) if summary_parts else "No new evidence"


def summarize_execution_results(
    execution_results: dict,
    current_evidence: dict[str, Any],
    executed_hypotheses: list[dict[str, Any]],
    investigation_loop_count: int,
    rationale: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """
    Summarize execution results into evidence and hypotheses.

    Args:
        execution_results: Results from action execution
        current_evidence: Current evidence dictionary
        executed_hypotheses: History of executed hypotheses
        investigation_loop_count: Current loop count
        rationale: Rationale for executing these actions

    Returns:
        Tuple of (evidence, executed_hypotheses, evidence_summary)
    """
    evidence = merge_evidence(current_evidence, execution_results)

    # Only track successful actions in hypothesis history (allow retries of failed actions)
    successful_actions = [
        action_name for action_name, result in execution_results.items() if result.success
    ]

    if successful_actions:
        executed_hypotheses = track_hypothesis(
            executed_hypotheses, successful_actions, rationale, investigation_loop_count
        )

    evidence_summary = build_evidence_summary(execution_results)

    return evidence, executed_hypotheses, evidence_summary
