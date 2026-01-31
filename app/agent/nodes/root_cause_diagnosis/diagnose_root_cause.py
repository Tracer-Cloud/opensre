"""Simplified root cause diagnosis with integrated validation.

This node analyzes evidence and determines root cause.
It updates state fields but does NOT render output directly.
"""

import json

from langsmith import traceable

from app.agent.output import debug_print, get_tracker
from app.agent.state import InvestigationState
from app.agent.tools.clients import get_llm, parse_root_cause


@traceable(name="diagnose_root_cause")
def main(state: InvestigationState) -> dict:
    """
    Simplified root cause diagnosis with integrated validation.

    Flow:
    1) Check if evidence is available
    2) Build simple prompt from evidence
    3) Call LLM to get root cause
    4) Validate claims against evidence
    5) Calculate confidence and validity
    """
    tracker = get_tracker()
    tracker.start("diagnose_root_cause", "Analyzing evidence")

    context = state.get("context", {})
    evidence = state.get("evidence", {})
    web_run = context.get("tracer_web_run", {})
    raw_alert = state.get("raw_alert", {})

    # Check if we have evidence from various sources
    has_tracer_evidence = web_run.get("found")
    has_cloudwatch_evidence = bool(evidence.get("error_logs") or evidence.get("cloudwatch_logs"))

    # Check for evidence in alert annotations (S3 logs, log excerpts, etc.)
    has_alert_evidence = False
    if isinstance(raw_alert, dict):
        annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {})
        if annotations:
            has_alert_evidence = bool(
                annotations.get("log_excerpt")
                or annotations.get("failed_steps")
                or annotations.get("error")
                or annotations.get("cloudwatch_logs_url")
            )

    # Check for evidence in alert annotations (S3 logs, log excerpts, etc.)
    has_alert_evidence = False
    if isinstance(raw_alert, dict):
        annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {})
        if annotations:
            has_alert_evidence = bool(
                annotations.get("log_excerpt")
                or annotations.get("failed_steps")
                or annotations.get("error")
                or annotations.get("cloudwatch_logs_url")
            )

    # If no evidence at all, return low-confidence result instead of crashing
    if not has_tracer_evidence and not has_cloudwatch_evidence and not has_alert_evidence:
        debug_print("Warning: Limited evidence available - proceeding with low confidence")

        # Return a basic result with low confidence
        problem = state.get("problem_md", "Pipeline failure detected")
        return {
            "root_cause": f"{problem}. Limited evidence available for analysis - unable to determine exact root cause without additional diagnostic data.",
            "confidence": 0.2,
            "validated_claims": [],
            "non_validated_claims": [
                {
                    "claim": "Insufficient evidence available to validate root cause",
                    "validation_status": "not_validated",
                }
            ],
            "validity_score": 0.0,
            "investigation_recommendations": [
                "Collect error logs from pipeline execution",
                "Check CloudWatch logs if available",
                "Review pipeline step exit codes and error messages",
            ],
            "investigation_loop_count": state.get("investigation_loop_count", 0),
        }

    # Build simple prompt from context and evidence
    prompt = _build_simple_prompt(state, evidence)

    # Call LLM
    debug_print("Invoking LLM for root cause analysis...")
    llm = get_llm()
    response = llm.with_config(run_name="LLM – Analyze evidence and propose root cause").invoke(
        prompt
    )
    response_text = response.content if hasattr(response, "content") else str(response)

    # Parse response
    result = parse_root_cause(response_text)

    # Simple validation: check if claims reference available evidence
    validated_claims_list = []
    non_validated_claims_list = []

    for claim in result.validated_claims:
        is_valid = _simple_validate_claim(claim, evidence)
        validated_claims_list.append(
            {
                "claim": claim,
                "evidence_sources": _extract_evidence_sources(claim, evidence),
                "validation_status": "validated" if is_valid else "failed_validation",
            }
        )

    for claim in result.non_validated_claims:
        is_valid = _simple_validate_claim(claim, evidence)
        if is_valid:
            validated_claims_list.append(
                {
                    "claim": claim,
                    "evidence_sources": _extract_evidence_sources(claim, evidence),
                    "validation_status": "validated",
                }
            )
        else:
            non_validated_claims_list.append(
                {
                    "claim": claim,
                    "validation_status": "not_validated",
                }
            )

    # Calculate validity score
    total_claims = len(validated_claims_list) + len(non_validated_claims_list)
    validity_score = len(validated_claims_list) / total_claims if total_claims > 0 else 0.0

    # Update confidence based on validity
    final_confidence = (result.confidence * 0.4) + (validity_score * 0.6)

    # Generate recommendations if confidence is low OR vendor evidence is missing
    investigation_recommendations = []
    loop_count = state.get("investigation_loop_count", 0)

    # Check if vendor evidence is missing (critical for upstream/downstream tracing)
    # Vendor evidence is present only if we have actual audit content, not just metadata pointing to it
    vendor_evidence_missing = not (
        evidence.get("vendor_audit_from_logs")  # Parsed from Lambda logs
        or (
            evidence.get("s3_audit_payload", {}).get("found")
            and evidence.get("s3_audit_payload", {}).get("content")
        )  # Actual audit payload fetched
    )

    print(
        f"[DEBUG] Vendor evidence check: vendor_missing={vendor_evidence_missing}, "
        f"confidence={final_confidence:.2f}, validity={validity_score:.2f}"
    )

    if final_confidence < 0.6 or validity_score < 0.5 or vendor_evidence_missing:
        investigation_recommendations = _generate_simple_recommendations(
            non_validated_claims_list, evidence
        )
        print(
            f"[DEBUG] Generated {len(investigation_recommendations)} recommendations: {investigation_recommendations}"
        )
        # Don't increment loop_count here - let routing do it after deciding to loop
        if investigation_recommendations:
            debug_print(
                f"Generated {len(investigation_recommendations)} recommendations for next loop"
            )

    tracker.complete(
        "diagnose_root_cause",
        fields_updated=["root_cause", "confidence", "validated_claims", "validity_score"],
        message=f"confidence:{final_confidence:.0%}, validity:{validity_score:.0%}",
    )

    return {
        "root_cause": result.root_cause,
        "confidence": final_confidence,
        "validated_claims": validated_claims_list,
        "non_validated_claims": non_validated_claims_list,
        "validity_score": validity_score,
        "investigation_recommendations": investigation_recommendations,
        "investigation_loop_count": loop_count,
    }


def _build_simple_prompt(state: InvestigationState, evidence: dict) -> str:
    """Build an evidence-based prompt for root cause analysis."""
    problem = state.get("problem_md", "")
    hypotheses = state.get("hypotheses", [])

    # Allowed evidence sources the model can reference (keeps grounding consistent)
    allowed_sources = [
        "aws_batch_jobs",
        "tracer_tools",
        "logs",
        "cloudwatch_logs",
        "host_metrics",
        "lambda_logs",
        "lambda_code",
        "lambda_config",
        "s3_metadata",
        "s3_audit",
        "vendor_audit",
    ]

    # Extract key investigation findings from evidence
    failed_jobs = evidence.get("failed_jobs", [])
    failed_tools = evidence.get("failed_tools", [])
    error_logs = evidence.get("error_logs", [])[:10]  # Limit to 10 most recent
    cloudwatch_logs = evidence.get("cloudwatch_logs", [])[:5]  # CloudWatch error logs
    host_metrics = evidence.get("host_metrics", {})
    lambda_logs = evidence.get("lambda_logs", [])[:10]  # Lambda execution logs
    lambda_function = evidence.get("lambda_function", {})
    lambda_config = evidence.get("lambda_config", {})
    s3_object = evidence.get("s3_object", {})
    s3_audit_payload = evidence.get("s3_audit_payload", {})
    vendor_audit_from_logs = evidence.get("vendor_audit_from_logs", {})

    # Extract evidence from alert annotations
    raw_alert = state.get("raw_alert", {})
    cloudwatch_url = None
    alert_annotations = {}
    if isinstance(raw_alert, dict):
        cloudwatch_url = raw_alert.get("cloudwatch_logs_url") or raw_alert.get("cloudwatch_url")
        alert_annotations = (
            raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {}) or {}
        )

    prompt = f"""You are an experienced SRE writing a short RCA (root cause analysis) for a data pipeline incident.

Goal: Be helpful and accurate. Prefer evidence-backed explanations over speculation.
If the exact root cause cannot be proven, provide the most likely explanation based on observed evidence,
and clearly state what is unknown.

DEFINITIONS:
- VALIDATED_CLAIMS: Directly supported by the evidence shown below (observed facts).
- NON_VALIDATED_CLAIMS: Plausible hypotheses or contributing factors that are NOT directly proven by the evidence.

RULES:
- Do NOT introduce external domain knowledge that is not visible in the evidence (e.g., what a tool usually does).
- Do NOT reference source code files or line numbers unless they appear explicitly in the log evidence below.
- You can ONLY use information present in the evidence logs shown below. If a traceback shows file names and line numbers, you may reference them.
- VALIDATED_CLAIMS should be factual and specific (no "maybe", "likely", "appears").
- NON_VALIDATED_CLAIMS may include "likely/maybe", but must stay consistent with evidence.
- Keep each claim to one sentence.
- When possible, mention which evidence source supports a validated claim using one of:
  {", ".join(allowed_sources)}.

PROBLEM:
{problem}

HYPOTHESES TO CONSIDER (may be incomplete):
{chr(10).join(f"- {h}" for h in hypotheses[:5]) if hypotheses else "- None"}

EVIDENCE:
"""

    if cloudwatch_logs:
        prompt += f"\nCloudWatch Error Logs ({len(cloudwatch_logs)} events):\n"
        for log in cloudwatch_logs:
            prompt += f"{log}\n"
        if cloudwatch_url:
            prompt += f"\n[Citation: View full logs at {cloudwatch_url}]\n"
        prompt += "\n"

    if failed_jobs:
        prompt += f"\nAWS Batch Failed Jobs ({len(failed_jobs)}):\n"
        for job in failed_jobs[:5]:
            prompt += (
                f"- {job.get('job_name', 'Unknown')}: {job.get('status_reason', 'No reason')}\n"
            )
    else:
        prompt += "\nAWS Batch Failed Jobs: None\n"

    if failed_tools:
        prompt += f"\nFailed Tools ({len(failed_tools)}):\n"
        for tool in failed_tools[:5]:
            prompt += f"- {tool.get('tool_name', 'Unknown')}: exit_code={tool.get('exit_code')}\n"
    else:
        prompt += "\nFailed Tools: None\n"

    if error_logs:
        prompt += f"\nError Logs ({len(error_logs)}):\n"
        for log in error_logs[:5]:
            prompt += f"- {log.get('message', '')[:200]}\n"
    else:
        prompt += "\nError Logs: None\n"

    if host_metrics and host_metrics.get("data"):
        prompt += "\nHost Metrics: Available (CPU, memory, disk)\n"
    else:
        prompt += "\nHost Metrics: None\n"

    if lambda_logs:
        prompt += f"\nLambda Invocation Logs ({len(lambda_logs)} events):\n"
        for log in lambda_logs[:10]:
            message = log.get("message", "") if isinstance(log, dict) else str(log)
            prompt += f"- {message[:300]}\n"

    if lambda_function and lambda_function.get("function_name"):
        prompt += "\nLambda Function Configuration:\n"
        prompt += f"- Function: {lambda_function.get('function_name')}\n"
        prompt += f"- Runtime: {lambda_function.get('runtime')}\n"
        prompt += f"- Handler: {lambda_function.get('handler')}\n"
        if lambda_function.get("environment_variables"):
            env_vars = lambda_function.get("environment_variables", {})
            prompt += f"- Environment Variables: {', '.join(env_vars.keys())}\n"
        if lambda_function.get("code", {}).get("files"):
            code_files = lambda_function.get("code", {}).get("files", {})
            if isinstance(code_files, dict) and code_files:
                prompt += f"- Code Files: {', '.join(list(code_files.keys())[:5])}\n"
                # Include handler code snippet if available
                handler_file = lambda_function.get("handler", "").split(".")[0] + ".py"
                if handler_file in code_files:
                    file_content = code_files.get(handler_file, "")
                    if isinstance(file_content, str):
                        code_snippet = file_content[:1000]
                        prompt += f"\nHandler Code Snippet ({handler_file}):\n{code_snippet}\n"

    if lambda_config and lambda_config.get("function_name"):
        prompt += "\nLambda Configuration:\n"
        prompt += f"- Function: {lambda_config.get('function_name')}\n"
        prompt += f"- Runtime: {lambda_config.get('runtime')}\n"
        prompt += f"- Handler: {lambda_config.get('handler')}\n"
        env_vars = lambda_config.get("environment_variables", {})
        if env_vars:
            prompt += "- Environment Variables:\n"
            for key, value in list(env_vars.items())[:10]:
                prompt += f"  - {key}: {value}\n"

    if s3_object and s3_object.get("found"):
        prompt += "\nS3 Object Details:\n"
        prompt += f"- Bucket: {s3_object.get('bucket')}\n"
        prompt += f"- Key: {s3_object.get('key')}\n"
        prompt += f"- Size: {s3_object.get('size')} bytes\n"
        prompt += f"- Content Type: {s3_object.get('content_type')}\n"
        metadata = s3_object.get("metadata", {})
        if metadata:
            prompt += f"- Metadata: {json.dumps(metadata, indent=2)}\n"
        if s3_object.get("sample") and s3_object.get("is_text"):
            prompt += f"\nS3 Object Sample:\n{s3_object.get('sample')[:500]}\n"

    if s3_audit_payload and s3_audit_payload.get("found"):
        prompt += "\nS3 Audit Payload (External API Lineage):\n"
        prompt += f"- Bucket: {s3_audit_payload.get('bucket')}\n"
        prompt += f"- Key: {s3_audit_payload.get('key')}\n"
        audit_content = s3_audit_payload.get("content")
        if audit_content:
            try:
                audit_data = (
                    json.loads(audit_content) if isinstance(audit_content, str) else audit_content
                )
                prompt += f"- Content: {json.dumps(audit_data, indent=2)[:1500]}\n"
            except (json.JSONDecodeError, TypeError):
                prompt += f"- Content: {str(audit_content)[:500]}\n"

    if vendor_audit_from_logs and vendor_audit_from_logs.get("requests"):
        prompt += "\nExternal Vendor API Audit (from Lambda logs):\n"
        for req in vendor_audit_from_logs.get("requests", [])[:5]:
            prompt += f"- {req.get('type')} {req.get('url')}\n"
            prompt += f"  Status: {req.get('status_code')}\n"
            if req.get("response_body"):
                response_str = json.dumps(req.get("response_body"), indent=2)[:500]
                prompt += f"  Response: {response_str}\n"

    # Include alert annotation evidence (log excerpts, failed steps, etc.)
    if alert_annotations.get("log_excerpt"):
        prompt += f"\nLog Excerpt from Alert:\n{alert_annotations['log_excerpt'][:1000]}\n"
    if alert_annotations.get("failed_steps"):
        prompt += f"\nFailed Steps Summary:\n{alert_annotations['failed_steps']}\n"
    if alert_annotations.get("error"):
        prompt += f"\nError Message:\n{alert_annotations['error']}\n"

    prompt += f"""
OUTPUT FORMAT (follow exactly):

ROOT_CAUSE:
<1–2 sentences. If not proven, say "Most likely ..." and state what's missing. Do not say only "Unable to determine".>

VALIDATED_CLAIMS:
- <one factual claim> [evidence: <one of {", ".join(allowed_sources)}>]
- <another factual claim> [evidence: <one of {", ".join(allowed_sources)}>]

NON_VALIDATED_CLAIMS:
- <one plausible hypothesis consistent with evidence>
- <another plausible hypothesis>
(If you include hypotheses, focus on explaining the failure mechanism and what data is missing to confirm it.)

CONFIDENCE: <0-100 integer>
"""

    return prompt


def _simple_validate_claim(claim: str, evidence: dict) -> bool:
    """Simple validation: check if claim references available evidence."""
    claim_lower = claim.lower()

    # Check logs (from evidence)
    if ("log" in claim_lower or "error" in claim_lower) and evidence.get("total_logs", 0) == 0:
        return False

    # Check metrics (from evidence)
    if ("memory" in claim_lower or "cpu" in claim_lower) and not evidence.get(
        "host_metrics", {}
    ).get("data"):
        return False

    # Check jobs (from evidence)
    if ("job" in claim_lower or "batch" in claim_lower) and len(
        evidence.get("failed_jobs", [])
    ) == 0:
        return False

    # Check lambda evidence
    if ("lambda" in claim_lower or "function" in claim_lower) and not (
        evidence.get("lambda_logs") or evidence.get("lambda_function")
    ):
        return False

    # Check s3 evidence
    if ("s3" in claim_lower or "bucket" in claim_lower or "object" in claim_lower) and not (
        evidence.get("s3_object") or evidence.get("s3_objects")
    ):
        return False

    # Check schema/metadata evidence
    if ("schema" in claim_lower or "metadata" in claim_lower) and not evidence.get(
        "s3_object", {}
    ).get("metadata"):
        return False

    # Check vendor/external API evidence
    return not (
        ("vendor" in claim_lower or "external api" in claim_lower or "api" in claim_lower)
        and not (
            evidence.get("vendor_audit_from_logs")
            or evidence.get("s3_audit_payload")
            or evidence.get("lambda_config", {}).get("environment_variables")
        )
    )


def _extract_evidence_sources(claim: str, evidence: dict) -> list[str]:
    """Extract evidence sources mentioned in a claim."""
    sources = []
    claim_lower = claim.lower()

    if ("log" in claim_lower or "error" in claim_lower) and evidence.get("total_logs", 0) > 0:
        sources.append("logs")
    if ("job" in claim_lower or "batch" in claim_lower) and evidence.get("failed_jobs"):
        sources.append("aws_batch_jobs")
    if "tool" in claim_lower and evidence.get("failed_tools"):
        sources.append("tracer_tools")
    if (
        "metric" in claim_lower or "memory" in claim_lower or "cpu" in claim_lower
    ) and evidence.get("host_metrics", {}).get("data"):
        sources.append("host_metrics")
    if ("lambda" in claim_lower or "function" in claim_lower) and (
        evidence.get("lambda_logs") or evidence.get("lambda_function")
    ):
        sources.append("lambda_logs")
    if "code" in claim_lower and evidence.get("lambda_function", {}).get("code"):
        sources.append("lambda_code")
    if ("s3" in claim_lower or "bucket" in claim_lower or "object" in claim_lower) and evidence.get(
        "s3_object"
    ):
        sources.append("s3_metadata")
    if ("schema" in claim_lower or "metadata" in claim_lower) and evidence.get("s3_object", {}).get(
        "metadata"
    ):
        sources.append("s3_metadata")
    if ("vendor" in claim_lower or "external" in claim_lower or "api" in claim_lower) and (
        evidence.get("vendor_audit_from_logs") or evidence.get("s3_audit_payload")
    ):
        sources.append("vendor_audit")
    if ("environment" in claim_lower or "env" in claim_lower) and evidence.get(
        "lambda_config", {}
    ).get("environment_variables"):
        sources.append("lambda_config")
    if "audit" in claim_lower and evidence.get("s3_audit_payload"):
        sources.append("s3_audit")

    return sources if sources else ["evidence_analysis"]


def _generate_simple_recommendations(non_validated_claims: list[dict], evidence: dict) -> list[str]:
    """Generate simple investigation recommendations."""
    if not non_validated_claims:
        return []

    recommendations = []

    # Check what's missing (investigation findings from evidence)
    if not evidence.get("host_metrics", {}).get("data"):
        recommendations.append("Query CloudWatch Metrics for CPU and memory usage")
    if evidence.get("total_logs", 0) == 0:
        recommendations.append("Fetch CloudWatch Logs for detailed error messages")
    if not evidence.get("failed_jobs"):
        recommendations.append("Query AWS Batch job details using describe_jobs API")

    # Check for upstream/vendor evidence gaps
    if not evidence.get("s3_object", {}).get("metadata"):
        recommendations.append("Inspect S3 object to get metadata and trace data lineage")
    if not evidence.get("vendor_audit_from_logs") and not evidence.get("s3_audit_payload"):
        recommendations.append("Retrieve Lambda configuration and logs from upstream functions")
    if evidence.get("s3_object", {}).get("metadata", {}).get("audit_key") and not evidence.get(
        "s3_audit_payload"
    ):
        recommendations.append("Fetch S3 audit payload to trace external vendor interactions")
    if not evidence.get("lambda_config") and not evidence.get("lambda_function"):
        recommendations.append(
            "Get Lambda function configuration to identify external dependencies"
        )

    return recommendations[:5]


@traceable(name="node_diagnose_root_cause")
def node_diagnose_root_cause(state: InvestigationState) -> dict:
    """LangGraph node wrapper with LangSmith tracking."""
    return main(state)
