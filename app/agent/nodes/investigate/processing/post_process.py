"""Post-processing: merge evidence and track hypotheses."""

import json
from typing import Any


def _parse_vendor_audit_from_logs(logs: list) -> dict | None:
    """Extract EXTERNAL_API_AUDIT structured logs from Lambda logs."""
    for log_entry in logs:
        message = log_entry.get("message", "") if isinstance(log_entry, dict) else str(log_entry)
        if "EXTERNAL_API_AUDIT:" in message:
            try:
                audit_json = message.split("EXTERNAL_API_AUDIT:", 1)[1].strip()
                return json.loads(audit_json)
            except (json.JSONDecodeError, IndexError):
                continue
    return None


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

        data = result.data

        if action_name == "get_failed_jobs":
            evidence["failed_jobs"] = data.get("failed_jobs", [])
            evidence["total_jobs"] = data.get("total_jobs", 0)

        elif action_name == "get_failed_tools":
            evidence["failed_tools"] = data.get("failed_tools", [])
            evidence["total_tools"] = data.get("total_tools", 0)

        elif action_name == "get_error_logs":
            evidence["error_logs"] = data.get("logs", [])
            evidence["total_logs"] = data.get("total_logs", 0)

        elif action_name == "get_host_metrics":
            evidence["host_metrics"] = data.get("metrics", {})

        elif action_name == "get_cloudwatch_logs":
            evidence["cloudwatch_logs"] = data.get("error_logs", [])
            evidence["cloudwatch_event_count"] = data.get("event_count", 0)
            evidence["cloudwatch_latest_error"] = data.get("latest_error")

        elif action_name == "inspect_s3_object":
            evidence["s3_object"] = {
                "bucket": data.get("bucket"),
                "key": data.get("key"),
                "found": data.get("found", False),
                "size": data.get("size"),
                "content_type": data.get("content_type"),
                "metadata": data.get("metadata", {}),
                "sample": data.get("sample"),
                "is_text": data.get("is_text", False),
            }

        elif action_name == "list_s3_objects":
            evidence["s3_objects"] = data.get("objects", [])
            evidence["s3_object_count"] = data.get("count", 0)

        elif action_name == "get_lambda_invocation_logs":
            evidence["lambda_logs"] = data.get("recent_logs", [])
            evidence["lambda_invocation_count"] = data.get("invocation_count", 0)
            evidence["lambda_invocations"] = data.get("invocations", [])
            # Parse vendor audit from logs
            vendor_audit = _parse_vendor_audit_from_logs(data.get("recent_logs", []))
            if vendor_audit:
                evidence["vendor_audit_from_logs"] = vendor_audit

        elif action_name == "get_lambda_errors":
            evidence["lambda_errors"] = data.get("recent_logs", [])
            evidence["lambda_error_count"] = data.get("invocation_count", 0)

        elif action_name == "inspect_lambda_function":
            evidence["lambda_function"] = {
                "function_name": data.get("function_name"),
                "runtime": data.get("runtime"),
                "handler": data.get("handler"),
                "timeout": data.get("timeout"),
                "memory_size": data.get("memory_size"),
                "environment_variables": data.get("environment_variables", {}),
                "code": data.get("code", {}),
            }

        elif action_name == "get_lambda_configuration":
            evidence["lambda_config"] = {
                "function_name": data.get("function_name"),
                "runtime": data.get("runtime"),
                "handler": data.get("handler"),
                "timeout": data.get("timeout"),
                "memory_size": data.get("memory_size"),
                "environment_variables": data.get("environment_variables", {}),
            }

        elif action_name == "get_s3_object":
            evidence["s3_audit_payload"] = {
                "bucket": data.get("bucket"),
                "key": data.get("key"),
                "found": data.get("found", False),
                "content": data.get("content"),
                "metadata": data.get("metadata", {}),
            }

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

    return ", ".join(summary_parts) if summary_parts else "No new evidence"


def summarize_execution_results(
    execution_results: dict,
    action_names: list[str],
    current_evidence: dict[str, Any],
    executed_hypotheses: list[dict[str, Any]],
    investigation_loop_count: int,
    rationale: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """
    Summarize execution results into evidence and hypotheses.

    Args:
        execution_results: Results from action execution
        action_names: List of actions that were executed
        current_evidence: Current evidence dictionary
        executed_hypotheses: History of executed hypotheses
        investigation_loop_count: Current loop count
        rationale: Rationale for executing these actions

    Returns:
        Tuple of (evidence, executed_hypotheses, evidence_summary)
    """
    evidence = merge_evidence(current_evidence, execution_results)
    executed_hypotheses = track_hypothesis(
        executed_hypotheses, action_names, rationale, investigation_loop_count
    )
    evidence_summary = build_evidence_summary(execution_results)

    return evidence, executed_hypotheses, evidence_summary
