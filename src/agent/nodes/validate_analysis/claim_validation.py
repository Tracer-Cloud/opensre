"""Claim validation logic."""

import re

from src.agent.tools.llm import get_llm


def validate_claim_against_evidence(
    claim: str, evidence: dict, investigation: dict
) -> tuple[bool, list[str]]:
    """
    Validate a single claim against available evidence.

    Returns:
        (is_valid, issues) - whether claim is validated and any issues found
    """
    issues = []
    claim_lower = claim.lower()

    # Check for unrealistic values
    percent_pattern = r"(\d+(?:\.\d+)?)\s*%"
    percentages = re.findall(percent_pattern, claim, re.IGNORECASE)
    for pct_str in percentages:
        try:
            pct = float(pct_str)
            if pct > 100:
                issues.append(f"Impossible percentage: {pct}%")
                return False, issues
        except ValueError:
            pass

    # Check for impossible memory usage
    memory_patterns = [
        r"(\d+(?:\.\d+)?)\s*(?:billion|million)\s*(?:percent|%)",
        r"(\d+(?:\.\d+)?)\+?\s*billion\s*(?:percent|%)",
    ]
    for pattern in memory_patterns:
        if re.search(pattern, claim, re.IGNORECASE):
            issues.append("Impossible memory usage value")
            return False, issues

    # Check if claim references evidence that exists
    web_run = evidence.get("tracer_web_run", {})

    # Check logs
    if ("log" in claim_lower or "error message" in claim_lower) and web_run.get(
        "total_logs", 0
    ) == 0:
        issues.append("Claim references logs but no logs were available")

    # Check metrics
    metrics = web_run.get("host_metrics", {})
    if ("memory" in claim_lower or "cpu" in claim_lower or "metric" in claim_lower) and (
        not metrics or not metrics.get("data")
    ):
        issues.append("Claim references metrics but metrics were not available")

    # Check jobs
    if ("job" in claim_lower or "batch" in claim_lower) and len(
        web_run.get("failed_jobs", [])
    ) == 0:
        # This might be valid if no jobs failed, but check if jobs were checked
        evidence_sources_checked = investigation.get("evidence_sources_checked", [])
        if "aws_batch_jobs" not in str(evidence_sources_checked):
            issues.append("Claim references jobs but job data was not checked")

    # Use LLM for deeper validation
    is_valid_llm, llm_issues = _validate_claim_with_llm(claim, evidence, investigation)
    if not is_valid_llm and llm_issues:
        issues.extend(llm_issues.split("\n") if isinstance(llm_issues, str) else llm_issues)

    return len(issues) == 0, issues


def _validate_claim_with_llm(claim: str, evidence: dict, investigation: dict) -> tuple[bool, str]:
    """Use LLM to validate a single claim."""
    evidence_summary = _build_evidence_summary(evidence, investigation)

    prompt = f"""Validate this root cause claim against the available evidence:

CLAIM: {claim}

AVAILABLE EVIDENCE:
{evidence_summary}

Is this claim directly supported by the evidence? Respond:
VALIDATED: yes/no
REASON: <brief explanation>

If VALIDATED: no, explain what evidence is missing or what contradicts the claim."""

    llm = get_llm()
    try:
        response = llm.invoke(prompt)
        response_text = response.content if hasattr(response, "content") else str(response)

        is_valid = "VALIDATED: yes" in response_text or "VALIDATED:yes" in response_text.lower()
        reason = ""
        if "REASON:" in response_text:
            reason = response_text.split("REASON:")[1].strip()
        elif not is_valid:
            reason = response_text.strip()

        return is_valid, reason
    except Exception:
        # If LLM validation fails, assume valid if no rule-based issues
        return True, ""


def _build_evidence_summary(evidence: dict, investigation: dict) -> str:
    """Build a summary of available evidence for claim validation."""
    web_run = evidence.get("tracer_web_run", {})
    summary = []

    if web_run.get("found"):
        summary.append(f"Pipeline: {web_run.get('pipeline_name')}, Status: {web_run.get('status')}")
        summary.append(f"Failed jobs: {len(web_run.get('failed_jobs', []))}")
        summary.append(f"Failed tools: {len(web_run.get('failed_tools', []))}")
        summary.append(f"Total logs: {web_run.get('total_logs', 0)}")
        summary.append(f"Error logs: {len(web_run.get('error_logs', []))}")

        metrics = web_run.get("host_metrics", {})
        if metrics and metrics.get("data"):
            summary.append("Host metrics: Available")
        else:
            summary.append("Host metrics: Not available")

    evidence_sources = investigation.get("evidence_sources_checked", [])
    summary.append(f"Evidence sources checked: {', '.join(evidence_sources)}")

    return "\n".join(summary)
