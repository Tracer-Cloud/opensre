"""Post-processing: merge evidence and track hypotheses."""

from app.agent.state import InvestigationState


def merge_evidence(
    state: InvestigationState, execution_results: dict
) -> dict:
    """
    Merge execution results into evidence state.

    Args:
        state: Current investigation state
        execution_results: Results from action execution

    Returns:
        Updated evidence dictionary
    """
    evidence = state.get("evidence", {}).copy()

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

    return evidence


def track_hypothesis(
    state: InvestigationState, action_names: list[str], rationale: str
) -> list[dict]:
    """
    Track executed hypothesis for deduplication.

    Args:
        state: Current investigation state
        action_names: List of actions that were executed
        rationale: Rationale for executing these actions

    Returns:
        Updated executed_hypotheses list
    """
    executed_hypotheses = state.get("executed_hypotheses", [])
    new_hypothesis = {
        "actions": action_names,
        "rationale": rationale,
        "loop_count": state.get("investigation_loop_count", 0),
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

    return ", ".join(summary_parts) if summary_parts else "No new evidence"
