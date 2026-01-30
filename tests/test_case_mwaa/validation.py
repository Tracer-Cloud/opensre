"""
MWAA Test Case - Full Trace Validation.

Validation checkpoints to verify the agent correctly traces failures
from MWAA to upstream components (S3, Lambda, External API).

These functions are used to validate investigation results against
expected outcomes for different failure scenarios.
"""

from typing import Any


def validate_mwaa_task_logs_collected(evidence: dict[str, Any]) -> dict[str, Any]:
    """
    Checkpoint 1: Validate MWAA task logs were collected.

    Verifies the agent retrieved task execution logs from CloudWatch.

    Args:
        evidence: Investigation evidence dict

    Returns:
        Validation result with pass/fail and details
    """
    task_logs = evidence.get("mwaa_task_logs") or evidence.get("airflow_task_logs")

    if not task_logs:
        return {
            "passed": False,
            "checkpoint": "mwaa_task_logs_collected",
            "message": "No MWAA task logs found in evidence",
            "expected": "Task logs from CloudWatch",
        }

    log_count = task_logs.get("event_count", 0) if isinstance(task_logs, dict) else len(task_logs)

    return {
        "passed": log_count > 0,
        "checkpoint": "mwaa_task_logs_collected",
        "message": f"Found {log_count} task log events",
        "expected": "At least 1 task log event",
        "actual": log_count,
    }


def validate_s3_object_inspected(evidence: dict[str, Any]) -> dict[str, Any]:
    """
    Checkpoint 2: Validate S3 data object was inspected.

    Verifies the agent inspected the S3 object to understand data issues.

    Args:
        evidence: Investigation evidence dict

    Returns:
        Validation result with pass/fail and details
    """
    s3_evidence = evidence.get("s3_object_metadata") or evidence.get("s3_object")

    if not s3_evidence:
        return {
            "passed": False,
            "checkpoint": "s3_object_inspected",
            "message": "No S3 object metadata found in evidence",
            "expected": "S3 object inspection results",
        }

    found = s3_evidence.get("found", False) if isinstance(s3_evidence, dict) else bool(s3_evidence)

    return {
        "passed": found,
        "checkpoint": "s3_object_inspected",
        "message": "S3 object was inspected" if found else "S3 object not found",
        "expected": "S3 object exists and was inspected",
        "actual": s3_evidence,
    }


def validate_lambda_logs_collected(evidence: dict[str, Any]) -> dict[str, Any]:
    """
    Checkpoint 3: Validate Lambda invocation logs were collected.

    Verifies the agent retrieved Lambda function logs to trace the data source.

    Args:
        evidence: Investigation evidence dict

    Returns:
        Validation result with pass/fail and details
    """
    lambda_logs = evidence.get("lambda_invocation_logs") or evidence.get("lambda_logs")

    if not lambda_logs:
        return {
            "passed": False,
            "checkpoint": "lambda_logs_collected",
            "message": "No Lambda invocation logs found in evidence",
            "expected": "Lambda function execution logs",
        }

    found = lambda_logs.get("found", False) if isinstance(lambda_logs, dict) else bool(lambda_logs)

    return {
        "passed": found,
        "checkpoint": "lambda_logs_collected",
        "message": "Lambda logs were collected" if found else "Lambda logs not found",
        "expected": "Lambda execution logs",
        "actual": lambda_logs.get("invocation_count", 0) if isinstance(lambda_logs, dict) else "present",
    }


def validate_schema_change_identified(result: dict[str, Any]) -> dict[str, Any]:
    """
    Checkpoint 4: Validate schema change was identified as root cause.

    Verifies the agent correctly identified the schema change.

    Args:
        result: Full investigation result

    Returns:
        Validation result with pass/fail and details
    """
    root_cause = result.get("root_cause", "").lower()

    schema_keywords = ["schema", "missing", "field", "customer_id", "column"]
    found_keywords = [kw for kw in schema_keywords if kw in root_cause]

    return {
        "passed": len(found_keywords) >= 2,
        "checkpoint": "schema_change_identified",
        "message": f"Found keywords: {found_keywords}" if found_keywords else "Schema change not identified",
        "expected": "Root cause mentions schema change or missing fields",
        "actual": root_cause[:200] if root_cause else "No root cause",
    }


def validate_full_trace(result: dict[str, Any]) -> dict[str, Any]:
    """
    Full trace validation - runs all checkpoints.

    Verifies the agent correctly traced the failure through all components:
    MWAA task logs -> S3 object -> Lambda logs -> Schema change

    Args:
        result: Full investigation result

    Returns:
        Comprehensive validation result
    """
    evidence = result.get("evidence", {})

    checkpoints = [
        validate_mwaa_task_logs_collected(evidence),
        validate_s3_object_inspected(evidence),
        validate_lambda_logs_collected(evidence),
        validate_schema_change_identified(result),
    ]

    passed_count = sum(1 for cp in checkpoints if cp["passed"])
    total_count = len(checkpoints)

    return {
        "overall_passed": passed_count == total_count,
        "passed_count": passed_count,
        "total_count": total_count,
        "pass_rate": passed_count / total_count if total_count > 0 else 0,
        "checkpoints": checkpoints,
        "summary": f"{passed_count}/{total_count} checkpoints passed",
    }


def validate_confidence_threshold(result: dict[str, Any], min_confidence: float = 0.6) -> dict[str, Any]:
    """
    Validate the investigation confidence meets minimum threshold.

    Args:
        result: Full investigation result
        min_confidence: Minimum acceptable confidence (0-1)

    Returns:
        Validation result
    """
    confidence = result.get("confidence", 0)

    return {
        "passed": confidence >= min_confidence,
        "checkpoint": "confidence_threshold",
        "message": f"Confidence {confidence:.0%} {'meets' if confidence >= min_confidence else 'below'} {min_confidence:.0%} threshold",
        "expected": f">= {min_confidence:.0%}",
        "actual": f"{confidence:.0%}",
    }


def run_validation_suite(result: dict[str, Any]) -> dict[str, Any]:
    """
    Run the complete validation suite for an investigation result.

    Args:
        result: Full investigation result

    Returns:
        Complete validation suite results
    """
    full_trace = validate_full_trace(result)
    confidence = validate_confidence_threshold(result)

    all_checkpoints = full_trace["checkpoints"] + [confidence]
    passed_count = sum(1 for cp in all_checkpoints if cp["passed"])

    return {
        "suite_passed": full_trace["overall_passed"] and confidence["passed"],
        "full_trace": full_trace,
        "confidence": confidence,
        "all_checkpoints": all_checkpoints,
        "summary": {
            "total_checkpoints": len(all_checkpoints),
            "passed": passed_count,
            "failed": len(all_checkpoints) - passed_count,
        },
    }
