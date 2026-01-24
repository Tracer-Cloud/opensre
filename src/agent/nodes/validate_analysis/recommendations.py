"""Generate AWS SDK investigation recommendations."""

from src.agent.tools.llm import get_llm


def generate_investigation_recommendations(
    validated_claims: list[dict],
    non_validated_claims: list[dict],
    evidence: dict,
    investigation: dict,
) -> list[str]:
    """
    Generate recommended AWS SDK investigations to increase validated claims.

    Returns list of recommended investigations using AWS SDK.
    """
    if not non_validated_claims:
        return []

    # Build summary of what's missing
    missing_evidence = []
    for claim_data in non_validated_claims:
        claim = claim_data.get("claim", "")
        issues = claim_data.get("validation_issues", [])
        if issues:
            missing_evidence.append(f"- {claim}: {', '.join(issues)}")

    evidence_summary = _build_evidence_gap_summary(evidence, investigation)
    non_validated_summary = "\n".join(missing_evidence[:5])  # Limit to 5 for prompt

    prompt = f"""You are an investigation planner. Generate specific AWS SDK investigation recommendations.

Current Situation:
- Validated claims: {len(validated_claims)}
- Non-validated claims: {len(non_validated_claims)}

Non-validated claims and missing evidence:
{non_validated_summary}

Current evidence available:
{evidence_summary}

Generate specific AWS SDK investigation recommendations that would help validate the non-validated claims.
Focus on:
1. AWS Batch job details (describe_jobs, list_jobs)
2. CloudWatch Logs (filter_log_events, get_log_events)
3. CloudWatch Metrics (get_metric_statistics, get_metric_data)
4. ECS task details (describe_tasks, describe_task_definition)
5. EC2 instance metrics (describe_instance_status, get_instance_metadata)

Format as a numbered list of specific AWS SDK calls to make.
Each recommendation should be actionable and specific.

RECOMMENDATIONS:
1. <specific AWS SDK call with parameters>
2. <specific AWS SDK call with parameters>
..."""

    llm = get_llm()
    try:
        response = llm.invoke(prompt)
        response_text = response.content if hasattr(response, "content") else str(response)

        # Parse recommendations
        recommendations = []
        if "RECOMMENDATIONS:" in response_text:
            recs_section = response_text.split("RECOMMENDATIONS:")[1].strip()
            for line in recs_section.split("\n"):
                line = line.strip()
                # Extract numbered items
                if line and (line[0].isdigit() or line.startswith("-") or line.startswith("*")):
                    clean_line = line.lstrip("0123456789.-* ").strip()
                    if clean_line:
                        recommendations.append(clean_line)
        else:
            # Fallback: extract bullet points
            for line in response_text.split("\n"):
                line = line.strip().lstrip("0123456789.-* ").strip()
                if line and len(line) > 20:  # Filter out short lines
                    recommendations.append(line)

        return recommendations[:10]  # Limit to 10 recommendations
    except Exception:
        # Fallback recommendations
        return [
            "Query AWS Batch job details using describe_jobs API",
            "Fetch CloudWatch Logs for failed job containers",
            "Retrieve CloudWatch Metrics for memory and CPU usage",
        ]


def _build_evidence_gap_summary(evidence: dict, investigation: dict) -> str:
    """Build summary of evidence gaps."""
    summary = []
    web_run = evidence.get("tracer_web_run", {})

    evidence_sources_checked = investigation.get("evidence_sources_checked", [])
    evidence_sources_skipped = investigation.get("evidence_sources_skipped", [])

    summary.append(f"Sources checked: {', '.join(evidence_sources_checked) if evidence_sources_checked else 'None'}")
    summary.append(f"Sources skipped: {', '.join(evidence_sources_skipped) if evidence_sources_skipped else 'None'}")

    if web_run.get("found"):
        summary.append(f"Logs available: {web_run.get('total_logs', 0)}")
        summary.append(f"Failed jobs: {len(web_run.get('failed_jobs', []))}")
        summary.append(f"Metrics available: {'Yes' if web_run.get('host_metrics', {}).get('data') else 'No'}")

    return "\n".join(summary)
