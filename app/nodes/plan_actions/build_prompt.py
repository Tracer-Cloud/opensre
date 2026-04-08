"""Investigation prompt construction with available actions."""

from typing import Any

from pydantic import BaseModel, ValidationError


def _get_executed_sources(executed_hypotheses: list[dict[str, Any]]) -> set[str]:
    """Extract executed sources from hypotheses history."""
    executed_sources_set = set()
    for h in executed_hypotheses:
        sources = h.get("sources", [])
        if isinstance(sources, list):
            executed_sources_set.update(sources)
        single_source = h.get("source")
        if single_source:
            executed_sources_set.add(single_source)
    return executed_sources_set


def _build_available_sources_hint(available_sources: dict[str, dict]) -> str:
    """
    Build hints for all available data sources.

    Args:
        available_sources: Dictionary mapping source type to parameters

    Returns:
        Formatted string with hints for available sources
    """
    hints = []

    if "cloudwatch" in available_sources:
        cw = available_sources["cloudwatch"]
        hints.append(
            f"""CloudWatch Logs Available:
- Log Group: {cw.get("log_group")}
- Log Stream: {cw.get("log_stream")}
- Region: {cw.get("region", "us-east-1")}
- Use get_cloudwatch_logs to fetch error logs and tracebacks"""
        )

    if "s3" in available_sources:
        s3 = available_sources["s3"]
        hints.append(
            f"""S3 Storage Available:
- Bucket: {s3.get("bucket")}
- Key: {s3.get("key", "N/A")}
- Prefix: {s3.get("prefix", "N/A")}
- Use inspect_s3_object to examine metadata and trace data lineage"""
        )

    if "s3_audit" in available_sources:
        s3_audit = available_sources["s3_audit"]
        hints.append(
            f"""S3 Audit Trail Available:
- Bucket: {s3_audit.get("bucket")}
- Key: {s3_audit.get("key")}
- Use get_s3_object to fetch audit payload with external API request/response details"""
        )

    if "s3_processed" in available_sources:
        s3_proc = available_sources["s3_processed"]
        hints.append(
            f"""S3 Processed Bucket Available:
- Bucket: {s3_proc.get("bucket")}
- Use check_s3_marker or list_s3_objects to verify output was created"""
        )

    if "local_file" in available_sources:
        local = available_sources["local_file"]
        hints.append(
            f"""Local File Available:
- Log File: {local.get("log_file")}
- Note: Local file logs can be read directly"""
        )

    if "tracer_web" in available_sources:
        tracer = available_sources["tracer_web"]
        hints.append(
            f"""Tracer Web Platform Available:
- Trace ID: {tracer.get("trace_id")}
- Run URL: {tracer.get("run_url", "N/A")}
- Use get_failed_jobs, get_failed_tools, get_error_logs to fetch execution data"""
        )

    if "aws_metadata" in available_sources:
        aws_meta = available_sources["aws_metadata"]
        metadata_items = [f"- {key}: {value}" for key, value in list(aws_meta.items())[:10]]
        hints.append(
            f"""AWS Infrastructure Metadata Available:
{chr(10).join(metadata_items)}
- Use this metadata to narrow the investigation and choose only the concrete AWS actions listed below"""
        )

    if "grafana" in available_sources:
        grafana = available_sources["grafana"]
        loki_only = grafana.get("loki_only", False)
        grafana_label = "Grafana Local (Loki only)" if loki_only else "Grafana Cloud"
        traces_hint = (
            "" if loki_only else "\n- Use query_grafana_traces to find distributed traces in Tempo"
        )
        hints.append(
            f"""{grafana_label} Available:
- Service Name: {grafana.get("service_name")}
- Pipeline: {grafana.get("pipeline_name")}
- Use query_grafana_logs to search Loki for pipeline errors{traces_hint}
- Use query_grafana_alert_rules to inspect alert configuration"""
        )

    if "datadog" in available_sources:
        dd = available_sources["datadog"]
        k8s_ctx = dd.get("kubernetes_context", {})
        k8s_hint = ""
        if k8s_ctx:
            k8s_parts = [f"{k}={v}" for k, v in k8s_ctx.items()]
            k8s_hint = f"\n- Kubernetes context: {', '.join(k8s_parts)}"
        hints.append(
            f"""Datadog Available:
- Pipeline: {dd.get("pipeline_name")}
- Default Query: {dd.get("default_query")}
- Site: {dd.get("site", "datadoghq.com")}{k8s_hint}
- Use query_datadog_logs to search for pipeline errors, PIPELINE_ERROR patterns, and application logs
- Use query_datadog_monitors to check monitor states and alerting configuration
- Use query_datadog_events to find deployments and infrastructure changes"""
        )

    if "vercel" in available_sources:
        vercel = available_sources["vercel"]
        hints.append(
            f"""Vercel Deployment Context Available:
- Project: {vercel.get("project_name") or vercel.get("project_slug") or vercel.get("project_id")}
- Deployment ID: {vercel.get("deployment_id") or "unknown"}
- Selected Log ID: {vercel.get("selected_log_id") or "not provided"}
- Log URL: {vercel.get("log_url") or "not provided"}
- Use vercel_deployment_status to inspect recent failed deployments and their git metadata
- Use vercel_deployment_logs to inspect build output and runtime logs for the deployment"""
        )

    if "github" in available_sources:
        github = available_sources["github"]
        hints.append(
            f"""GitHub Repository Context Available:
- Repository: {github.get("owner")}/{github.get("repo")}
- Commit SHA: {github.get("sha") or "unknown"}
- Ref: {github.get("ref") or "unknown"}
- Code Query: {github.get("query") or "exception OR error"}
- Use list_github_commits to correlate the deployment window with recent code changes
- Use search_github_code and get_github_file_contents to trace the failure into code"""
        )

    if "vercel" in available_sources and "github" in available_sources:
        hints.append(
            """Vercel And GitHub Correlation Available:
- Prioritise a deployment-to-code workflow
- First inspect Vercel deployment status and logs
- Then correlate the deployment commit SHA or ref with GitHub commits and code search results
- Prefer git evidence that matches the failing Vercel deployment over unrelated repository history"""
        )

    if "honeycomb" in available_sources:
        honeycomb = available_sources["honeycomb"]
        hints.append(
            f"""Honeycomb Available:
- Dataset: {honeycomb.get("dataset")}
- Service Name: {honeycomb.get("service_name") or "unknown"}
- Trace ID: {honeycomb.get("trace_id") or "unknown"}
- Use query_honeycomb_traces to inspect trace/span groups and identify slow or failing traces"""
        )

    if "coralogix" in available_sources:
        coralogix = available_sources["coralogix"]
        hints.append(
            f"""Coralogix Available:
- Application: {coralogix.get("application_name") or "unknown"}
- Subsystem: {coralogix.get("subsystem_name") or "unknown"}
- Default Query: {coralogix.get("default_query")}
- Use query_coralogix_logs to search Coralogix DataPrime logs for the failing service or error signature"""
        )

    if "eks" in available_sources:
        eks = available_sources["eks"]
        hints.append(
            f"""EKS Cluster Available:
- Cluster: {eks.get("cluster_name")}
- Namespace: {eks.get("namespace", "unknown")} (may not exist — verify with list_eks_namespaces)
- Pod: {eks.get("pod_name") or "unknown — use list_eks_pods to discover"}
- Deployment: {eks.get("deployment") or "unknown — use list_eks_deployments to discover"}
- Region: {eks.get("region", "us-east-1")}
IMPORTANT: Always start with discovery actions before fetching specific resources:
  1. list_eks_namespaces — confirm the namespace exists in the cluster
  2. list_eks_pods — discover what pods exist and which are failing/crashing
  3. list_eks_deployments — discover what deployments exist and which are degraded
  4. get_eks_events — get Warning events (OOMKilled, BackOff, FailedScheduling)
  5. get_eks_node_health — check node capacity and pressure conditions
  Only use get_eks_pod_logs / get_eks_deployment_status after confirming the resource exists."""
        )

    if "upstream_context" in available_sources:
        upstream = available_sources["upstream_context"]
        hints.append(
            f"""CAUSAL CHAIN DETECTED (confidence {upstream.get("causal_chain_confidence", 0.85):.0%}):
- {upstream.get("upstream_failure_hint")}
- Prioritise investigating whether this pipeline consumed bad or missing data from the upstream failure
- Check S3 input data timestamps and content for evidence of upstream data issues"""
        )

    if hints:
        return "\n\n" + "\n\n".join(hints) + "\n"
    return ""


def build_investigation_prompt(
    problem_md: str,
    executed_hypotheses: list[dict[str, Any]],
    available_actions: list,
    available_sources: dict[str, dict],
    memory_context: str = "",
) -> str:
    """
    Build the investigation prompt with rich action metadata.

    Args:
        problem_md: Problem statement markdown
        executed_hypotheses: History of executed hypotheses
        available_actions: Pre-computed actions list (already filtered by availability)
        available_sources: Dictionary of available data sources

    Returns:
        Formatted prompt string for LLM
    """
    executed_sources_set = _get_executed_sources(executed_hypotheses)
    executed_actions = [
        action.name for action in available_actions if action.source in executed_sources_set
    ]

    available_actions_filtered = [
        action for action in available_actions if action.name not in executed_actions
    ]

    problem_context = problem_md or "No problem statement available"

    actions_description = "\n\n".join(
        _format_action_metadata(action) for action in available_actions_filtered
    )

    sources_hint = _build_available_sources_hint(available_sources)

    # Build lineage investigation directive if S3 data is available
    lineage_directive = ""
    if available_sources.get("s3") or available_sources.get("s3_audit"):
        lineage_directive = """
**Upstream Tracing Strategy:**
For pipeline failures with S3 input data, follow this evidence chain to trace root cause:
1. Inspect S3 input object (inspect_s3_object) - get metadata: correlation_id, audit_key, schema_version, source Lambda
2. Fetch audit payload (get_s3_object with audit_key) - contains external API request/response details
3. Inspect source Lambda function (inspect_lambda_function) - get code and configuration
4. Correlate: external API schema changes → Lambda → S3 data → pipeline failure

This upstream trace reveals root causes outside the failed service (external API issues, upstream Lambda bugs, data quality problems).
"""

    # Add memory section with prior successful investigation paths
    memory_section = ""
    if memory_context:
        memory_section = f"""
**Prior Successful Investigation Paths (from memory):**
{memory_context[:1500]}

Use these proven investigation sequences as guidance for action selection.
"""

    prompt = f"""You are investigating a data pipeline incident.

Problem Context:
{problem_context}
{lineage_directive}
{memory_section}
{sources_hint}
Available Investigation Actions:
{actions_description if actions_description else "No actions available"}

Executed Actions: {", ".join(executed_actions) if executed_actions else "None"}

Task: Select the most relevant actions to execute now based on the problem context.
Consider what information would help diagnose the root cause.
"""
    return prompt


def apply_tool_budget(actions: list, budget: int) -> list:
    """
    Apply a tool budget to cap the number of actions selected.

    Args:
        actions: List of available actions
        budget: Maximum number of actions to allow

    Returns:
        Budget-capped list of actions
    """
    if len(actions) <= budget:
        return actions
    return actions[:budget]


def select_actions(
    actions: list,
    available_sources: dict[str, dict],
    executed_hypotheses: list[dict[str, Any]],
    tool_budget: int = 10,
) -> tuple[list, list[str]]:
    """
    Select available actions based on sources and execution history.

    Args:
        actions: Candidate actions to filter
        available_sources: Dictionary mapping source type to parameters
        executed_hypotheses: History of executed hypotheses
        tool_budget: Maximum number of tools to select (default: 10)

    Returns:
        Tuple of (available_actions, available_action_names)
    """
    available_actions = [action for action in actions if action.is_available(available_sources)]

    executed_actions_flat = set()
    for hyp in executed_hypotheses:
        actions_list = hyp.get("actions", [])
        if isinstance(actions_list, list):
            executed_actions_flat.update(actions_list)

    available_actions = [
        action for action in available_actions if action.name not in executed_actions_flat
    ]

    # Apply tool budget to cap the selected tool set
    available_actions = apply_tool_budget(available_actions, tool_budget)
    available_action_names = [action.name for action in available_actions]

    return available_actions, available_action_names


def plan_actions_with_llm(
    llm,
    plan_model: type[BaseModel],
    problem_md: str,
    executed_hypotheses: list[dict[str, Any]],
    available_actions: list,
    available_sources: dict[str, dict],
    memory_context: str = "",
):
    """
    Build the investigation prompt and invoke the LLM for a plan.

    Args:
        llm: LLM client
        plan_model: Pydantic model for structured output
        problem_md: Problem statement markdown
        executed_hypotheses: History of executed hypotheses
        available_actions: Filtered list of actions
        available_sources: Available data sources
        memory_context: Optional memory context from prior investigations

    Returns:
        Structured plan from the LLM
    """
    prompt = build_investigation_prompt(
        problem_md=problem_md,
        executed_hypotheses=executed_hypotheses,
        available_actions=available_actions,
        available_sources=available_sources,
        memory_context=memory_context,
    )

    # If memory context is provided, we're already using fast model from caller
    structured_llm = llm.with_structured_output(plan_model)
    try:
        return structured_llm.with_config(run_name="LLM – Plan evidence gathering").invoke(prompt)
    except (ValidationError, ValueError):
        fallback_actions = [action.name for action in available_actions][:3]
        rationale = "Fallback plan: LLM returned invalid structured output."
        return plan_model(actions=fallback_actions, rationale=rationale)


def _format_action_metadata(action) -> str:
    """Format a single action's metadata for the prompt."""
    inputs_desc = "\n    ".join(f"- {param}: {desc}" for param, desc in action.inputs.items())
    outputs_desc = "\n    ".join(f"- {field}: {desc}" for field, desc in action.outputs.items())
    use_cases_desc = "\n    ".join(f"- {uc}" for uc in action.use_cases)

    return f"""Action: {action.name}
  Description: {action.description}
  Source: {action.source}
  Required Inputs:
    {inputs_desc}
  Returns:
    {outputs_desc}
  Use When:
    {use_cases_desc}"""
