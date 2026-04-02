"""Infrastructure asset extraction and investigation trace building."""

from typing import Any

from app.nodes.publish_findings.formatters.base import format_slack_link
from app.nodes.publish_findings.report_context import ReportContext
from app.nodes.publish_findings.urls.aws import build_s3_console_url


def get_failed_pods(ctx: ReportContext) -> list[dict]:
    """Return failed pods list, falling back to single-pod fields."""
    pods: list[dict] = ctx.get("kube_failed_pods", [])
    if not pods:
        name = ctx.get("kube_pod_name")
        if name:
            pods = [{"pod_name": name, "namespace": ctx.get("kube_namespace"), "container": ctx.get("kube_container_name")}]
    return pods


def format_pod_line(pod: dict, datadog_site: str | None, *, bullet: str = "") -> str:
    """Format a single failed pod as a one-line string with a Datadog logs link.

    Returns empty string when pod has no name.
    """
    name = pod.get("pod_name") or pod.get("name")
    if not name:
        return ""

    ns = pod.get("namespace") or pod.get("kube_namespace")
    container = pod.get("container") or pod.get("container_name")
    exit_code = pod.get("exit_code")
    node = pod.get("node_name")
    node_ip = pod.get("node_ip")
    job = pod.get("kube_job")
    cluster = pod.get("cluster")
    mem_req = pod.get("memory_requested")
    mem_lim = pod.get("memory_limit")

    parts: list[str] = []
    if ns:
        parts.append(f"namespace={ns}")
    if container:
        parts.append(f"container={container}")
    if exit_code is not None:
        parts.append(f"exit={exit_code}")
    if cluster:
        parts.append(f"cluster={cluster}")
    if job:
        parts.append(f"job={job}")
    if node:
        parts.append(f"node={node} ({node_ip})" if node_ip else f"node={node}")
    if mem_req and mem_lim:
        parts.append(f"memory: requested={mem_req} limit={mem_lim}")
    elif mem_lim:
        parts.append(f"memory_limit={mem_lim}")

    meta = f" ({', '.join(parts)})" if parts else ""

    site = datadog_site or "datadoghq.com"
    if ns:
        query = f"kube_namespace:{ns} pod_name:{name}"
        url = f"https://app.{site}/logs?query={query.replace(' ', '+').replace(':', '%3A')}"
        pod_text = format_slack_link(name, url)
    else:
        pod_text = name

    return f"{bullet}{pod_text}{meta}"


def extract_infrastructure_assets(ctx: ReportContext) -> dict[str, Any]:
    """Extract infrastructure assets from alert annotations and evidence.

    Identifies all infrastructure components involved in the failure:
    - API Gateway
    - Lambda functions (primary, trigger, external)
    - S3 buckets (landing, processed, audit)
    - ECS/Fargate services
    - AWS Batch jobs
    - CloudWatch log groups
    - Pipeline metadata

    Args:
        ctx: Report context containing raw alert and evidence

    Returns:
        Dictionary of infrastructure assets organized by type
    """
    raw_alert = ctx.get("raw_alert", {})
    evidence = ctx.get("evidence", {})

    if not isinstance(raw_alert, dict):
        return {}

    # Extract annotations
    annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {})
    if not annotations and raw_alert.get("alerts"):
        first_alert = raw_alert.get("alerts", [{}])[0]
        if isinstance(first_alert, dict):
            annotations = first_alert.get("annotations", {}) or {}

    assets = {}

    # Extract API Gateway
    api_gateway = annotations.get("api_gateway") or annotations.get("api_gateway_id")
    if api_gateway:
        assets["api_gateway"] = api_gateway

    # Extract Lambda functions (multiple possible)
    lambda_functions = []

    # Primary Lambda function
    primary_lambda = (
        annotations.get("function_name")
        or annotations.get("lambda_function")
        or evidence.get("lambda_function", {}).get("function_name")
    )
    if primary_lambda:
        lambda_functions.append(
            {
                "name": primary_lambda,
                "runtime": evidence.get("lambda_function", {}).get("runtime"),
                "role": "primary",
            }
        )

    # Trigger Lambda (if different from primary)
    trigger_lambda = annotations.get("trigger_lambda") or annotations.get("ingestion_lambda")
    if trigger_lambda and trigger_lambda != primary_lambda:
        lambda_functions.append({"name": trigger_lambda, "runtime": None, "role": "trigger"})

    # External/Mock API Lambda
    external_lambda = annotations.get("external_api_lambda") or annotations.get("mock_api_lambda")
    if external_lambda:
        lambda_functions.append({"name": external_lambda, "runtime": None, "role": "external_api"})

    if lambda_functions:
        assets["lambda_functions"] = lambda_functions

    # Extract S3 buckets (landing and processed)
    s3_buckets = []

    landing_bucket = (
        annotations.get("landing_bucket")
        or annotations.get("s3_bucket")
        or annotations.get("bucket")
    )
    if landing_bucket:
        landing_key = annotations.get("s3_key") or annotations.get("key")
        s3_buckets.append({"name": landing_bucket, "key": landing_key, "type": "landing"})

    processed_bucket = annotations.get("processed_bucket") or annotations.get("output_bucket")
    if processed_bucket and processed_bucket != landing_bucket:
        s3_buckets.append({"name": processed_bucket, "key": None, "type": "processed"})

    audit_key = annotations.get("audit_key")
    if audit_key and landing_bucket:
        s3_buckets.append({"name": landing_bucket, "key": audit_key, "type": "audit"})

    if s3_buckets:
        assets["s3_buckets"] = s3_buckets

    # Extract ECS/Fargate info
    ecs_cluster = annotations.get("ecs_cluster")
    ecs_task = annotations.get("ecs_task_arn") or annotations.get("ecs_task")
    workflow_name = (
        annotations.get("airflow_dag")
        or annotations.get("dag_id")
        or annotations.get("prefect_flow")
        or annotations.get("flow_name")
    )

    if ecs_cluster or workflow_name:
        assets["ecs_service"] = {
            "cluster": ecs_cluster,
            "task": ecs_task,
            "flow_name": workflow_name,
        }

    # Extract AWS Batch info
    batch_job_queue = annotations.get("batch_job_queue") or evidence.get("batch_jobs", {}).get(
        "job_queue"
    )
    batch_job_definition = annotations.get("batch_job_definition")
    if batch_job_queue:
        assets["batch_service"] = {"queue": batch_job_queue, "definition": batch_job_definition}

    # Extract pipeline/workflow info (Prefect, Airflow, etc.)
    pipeline_name = ctx.get("pipeline_name")
    if pipeline_name and pipeline_name != "unknown":
        assets["pipeline"] = pipeline_name

    # Extract CloudWatch log groups (multiple possible)
    log_groups = []

    primary_log_group = ctx.get("cloudwatch_log_group")
    if primary_log_group:
        log_groups.append({"name": primary_log_group, "type": "primary"})

    lambda_log_group = annotations.get("lambda_log_group")
    if lambda_log_group and lambda_log_group != primary_log_group:
        log_groups.append({"name": lambda_log_group, "type": "lambda"})

    if log_groups:
        assets["log_groups"] = log_groups

    return assets


def build_investigation_trace(ctx: ReportContext) -> list[str]:
    """Build the investigation trace showing what was discovered.

    Creates a step-by-step narrative of the investigation path taken,
    showing the logical flow from failure detection to root cause.

    Args:
        ctx: Report context containing evidence and infrastructure assets

    Returns:
        List of trace step strings (numbered)
    """
    evidence = ctx.get("evidence", {})
    assets = extract_infrastructure_assets(ctx)
    trace_steps = []
    step_num = 1

    # Step 1: Where we detected the failure (logs)
    log_groups = assets.get("log_groups", [])
    if log_groups or evidence.get("cloudwatch_logs") or evidence.get("error_logs"):
        log_source = log_groups[0]["name"] if log_groups else "CloudWatch"
        trace_steps.append(f"{step_num}. Failure detected in {log_source}")
        step_num += 1

    # Kubernetes pods that experienced errors — show first 3, summarize the rest
    datadog_site = ctx.get("datadog_site", "datadoghq.com")
    all_pods = get_failed_pods(ctx)
    shown, total = 0, len(all_pods)
    for pod in all_pods[:3]:
        line = format_pod_line(pod, datadog_site)
        if line:
            trace_steps.append(f"{step_num}. Affected pod: {line}")
            step_num += 1
            shown += 1
    if total > 3:
        trace_steps.append(f"{step_num}. ... and {total - shown} more pods with the same failure")
        step_num += 1

    # Step 2: ECS/Batch/Lambda compute that failed
    if assets.get("ecs_service"):
        ecs = assets["ecs_service"]
        flow_name = ecs.get("flow_name")
        if flow_name:
            trace_steps.append(f"{step_num}. Workflow '{flow_name}' task failure identified")
        else:
            trace_steps.append(f"{step_num}. ECS task failure in {ecs.get('cluster', 'cluster')}")
        step_num += 1
    elif assets.get("batch_service"):
        batch = assets["batch_service"]
        trace_steps.append(f"{step_num}. AWS Batch job failed: {batch.get('queue', 'job')}")
        step_num += 1

    # Step 3: Lambda functions involved
    lambda_functions = assets.get("lambda_functions", [])
    if lambda_functions:
        for lf in lambda_functions:
            role = lf.get("role", "")
            name = lf["name"]
            if role == "trigger":
                trace_steps.append(f"{step_num}. Traced to trigger Lambda: {name}")
            elif role == "external_api":
                trace_steps.append(f"{step_num}. External API Lambda identified: {name}")
            elif role == "primary":
                trace_steps.append(f"{step_num}. Lambda function: {name}")
            step_num += 1

    # Step 4: S3 data inspection
    s3_buckets = assets.get("s3_buckets", [])
    if s3_buckets:
        region = ctx.get("cloudwatch_region") or "us-east-1"
        for bucket in s3_buckets:
            bucket_type = bucket.get("type", "")
            name = bucket["name"]
            key = bucket.get("key")

            if bucket_type == "landing" and key:
                s3_url = build_s3_console_url(name, key, region)
                trace_steps.append(
                    f"{step_num}. Input data inspected: {format_slack_link('S3 object', s3_url)}"
                )
                step_num += 1
            elif bucket_type == "audit" and key:
                s3_url = build_s3_console_url(name, key, region)
                trace_steps.append(
                    f"{step_num}. Audit trail found: {format_slack_link('S3 audit trail', s3_url)}"
                )
                step_num += 1

    s3_marker = ctx.get("s3_marker_exists")
    if s3_marker is True:
        trace_steps.append(f"{step_num}. Output verification: processed data exists")
        step_num += 1

    # Step 6: Root cause evidence
    if evidence.get("lambda_function"):
        trace_steps.append(f"{step_num}. Lambda configuration analyzed")
        step_num += 1

    return trace_steps


def format_infrastructure_correlation(ctx: ReportContext) -> str:
    """Format infrastructure correlation showing the investigation trace path.

    Args:
        ctx: Report context

    Returns:
        Formatted investigation trace section
    """
    trace_steps = build_investigation_trace(ctx)

    if not trace_steps:
        return ""

    lines = ["*Investigation Trace*"]
    lines.extend(trace_steps)

    return "\n" + "\n".join(lines) + "\n" if lines else ""
