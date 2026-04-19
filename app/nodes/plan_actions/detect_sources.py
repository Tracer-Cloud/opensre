"""Data source detection for dynamic investigation.

    Scans alert annotations and state context to detect available data sources
    (CloudWatch, S3, local files, Tracer Web, Grafana, Honeycomb, Coralogix, OpenClaw)
and extract their parameters.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from app.services.coralogix import build_coralogix_logs_query
from app.tools.GrafanaLogsTool import _map_pipeline_to_service_name

logger = logging.getLogger(__name__)


def _alert_time_range_minutes(raw_alert: dict[str, Any]) -> int:
    """Compute time_range_minutes to cover the alert window.

    Extracts startsAt from the alert payload (Alertmanager/Grafana format),
    calculates how many minutes ago the alert fired, and adds a 30-minute
    buffer so the query window starts before the alert and ends now.

    Falls back to 60 minutes if the timestamp is missing or unparseable.
    """
    starts_at: str | None = None

    # Alertmanager webhook: alerts[0].startsAt
    alerts = raw_alert.get("alerts", [])
    if alerts and isinstance(alerts, list):
        starts_at = alerts[0].get("startsAt")

    # Top-level field (some adapters flatten it)
    if not starts_at:
        starts_at = raw_alert.get("startsAt") or raw_alert.get("timestamp")

    # Annotations fallback
    if not starts_at:
        annotations = raw_alert.get("annotations") or raw_alert.get("commonAnnotations") or {}
        starts_at = annotations.get("timestamp")

    if not starts_at:
        return 60

    try:
        alert_time = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        # Zero-value sentinel from Alertmanager ("0001-01-01") — treat as no data
        if alert_time.year < 2000:
            return 60
        minutes_ago = int((datetime.now(UTC) - alert_time).total_seconds() / 60)
        # Window: from alert time - 5 min (pre-alert context) to now, minimum 60
        return max(60, minutes_ago + 35)
    except (ValueError, TypeError):
        return 60


def _alert_since_iso(raw_alert: dict[str, Any]) -> str:
    """Return an ISO 8601 timestamp for the start of the alert window."""
    starts_at: str | None = None

    alerts = raw_alert.get("alerts", [])
    if alerts and isinstance(alerts, list):
        starts_at = alerts[0].get("startsAt")
    if not starts_at:
        starts_at = raw_alert.get("startsAt") or raw_alert.get("timestamp")
    if not starts_at:
        annotations = raw_alert.get("annotations") or raw_alert.get("commonAnnotations") or {}
        starts_at = annotations.get("timestamp")

    if starts_at:
        try:
            alert_time = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
            if alert_time.year >= 2000:
                return (alert_time - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, TypeError):
            return (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    return (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _split_repo_full_name(value: str) -> tuple[str, str]:
    cleaned = value.strip().strip("/")
    if cleaned.count("/") < 1:
        return "", ""
    owner, repo = cleaned.split("/", 1)
    return owner.strip(), repo.strip().removesuffix(".git")


def _parse_repo_url(value: str) -> tuple[str, str]:
    parsed = urlparse(value.strip())
    if "github.com" not in parsed.netloc.lower():
        return "", ""
    path = parsed.path.strip("/")
    if not path:
        return "", ""
    parts = path.split("/")
    if len(parts) < 2:
        return "", ""
    return parts[0].strip(), parts[1].strip().removesuffix(".git")


def _parse_gitlab_repo_url(value: str) -> str:
    """Extract project_id (namespace/repo) from a GitLab URL."""
    parsed = urlparse(value.strip())
    if "gitlab" not in parsed.netloc.lower():
        return ""
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        return ""
    # Handle subgroups: return everything after the host as namespace/repo
    return "/".join(parts).removesuffix(".git")


def _extract_issue_id_from_url(value: str) -> str:
    parsed = urlparse(value.strip())
    parts = [part for part in parsed.path.split("/") if part]
    if "issues" not in parts:
        return ""
    index = parts.index("issues")
    if index + 1 >= len(parts):
        return ""
    return parts[index + 1].strip()


def detect_sources(
    raw_alert: dict[str, Any] | str,
    context: dict[str, Any],
    resolved_integrations: dict[str, Any] | None = None,
) -> dict[str, dict]:
    """
    Detect relevant data sources from alert annotations and context.

    Scans multiple locations for source information:
    - raw_alert.annotations
    - raw_alert.commonAnnotations
    - raw_alert top-level fields
    - context (for Tracer Web)
    - resolved_integrations (for Grafana credentials)

    Args:
        raw_alert: Raw alert payload (dict or str)
        context: Investigation context dictionary
        resolved_integrations: Pre-resolved integration credentials from resolve_integrations node

    Returns:
        Dictionary mapping source type to extracted parameters.
    """
    sources: dict[str, dict] = {}

    if isinstance(raw_alert, str):
        raw_alert = {}

    # Determine alert origin platform to avoid querying irrelevant integrations
    alert_source = raw_alert.get("alert_source", "").lower() if isinstance(raw_alert, dict) else ""

    # Structural detection: Grafana Alertmanager webhooks have externalURL pointing to Grafana
    if not alert_source and isinstance(raw_alert, dict):
        external_url = raw_alert.get("externalURL", "") or ""
        generator_url = raw_alert.get("generatorURL", "")
        alerts_list = raw_alert.get("alerts", []) or []
        first_gen = alerts_list[0].get("generatorURL", "") if alerts_list else ""
        candidate_urls = [external_url, generator_url, first_gen]
        if any("grafana" in str(u).lower() for u in candidate_urls):
            alert_source = "grafana"
        elif any("honeycomb" in str(u).lower() for u in candidate_urls):
            alert_source = "honeycomb"
        elif any("coralogix" in str(u).lower() for u in candidate_urls):
            alert_source = "coralogix"

    # Compute time window that covers the alert (used for Datadog/Grafana queries)
    alert_time_range_minutes = _alert_time_range_minutes(raw_alert)

    # Extract annotations from multiple possible locations.
    # Always merge top-level enriched fields (injected by _enrich_raw_alert) with
    # nested annotations so EKS/k8s fields extracted by the LLM are visible here.
    annotations: dict[str, Any] = {}
    if isinstance(raw_alert, dict):
        nested = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {}) or {}
        # Merge: nested annotations first, then top-level enriched fields override/fill gaps
        annotations = {**nested, **{k: v for k, v in raw_alert.items() if v and k not in nested}}

    # Detect CloudWatch sources
    cloudwatch_log_group = (
        annotations.get("cloudwatch_log_group")
        or annotations.get("log_group")
        or annotations.get("cloudwatchLogGroup")
        or annotations.get("lambda_log_group")  # Lambda-specific alias
    )
    cloudwatch_log_stream = (
        annotations.get("cloudwatch_log_stream")
        or annotations.get("log_stream")
        or annotations.get("cloudwatchLogStream")
    )

    if cloudwatch_log_group:
        cloudwatch_params: dict[str, str] = {
            "log_group": cloudwatch_log_group,
            "region": (
                annotations.get("cloudwatch_region")
                or annotations.get("aws_region")
                or annotations.get("region")
                or "us-east-1"
            ),
        }
        if cloudwatch_log_stream:
            cloudwatch_params["log_stream"] = cloudwatch_log_stream

        # Add correlation_id for log filtering if available
        correlation_id = annotations.get("correlation_id") or annotations.get("correlationId")
        if correlation_id:
            cloudwatch_params["correlation_id"] = correlation_id

        sources["cloudwatch"] = cloudwatch_params

    # Detect S3 sources (landing bucket)
    s3_bucket = (
        annotations.get("s3_bucket")
        or annotations.get("bucket")
        or annotations.get("s3Bucket")
        or annotations.get("landing_bucket")
    )
    s3_prefix = (
        annotations.get("s3_prefix") or annotations.get("prefix") or annotations.get("s3Prefix")
    )
    s3_key = annotations.get("s3_key") or annotations.get("key") or annotations.get("s3Key")

    if s3_bucket:
        s3_params: dict[str, str] = {"bucket": s3_bucket}
        if s3_prefix:
            s3_params["prefix"] = s3_prefix
        if s3_key:
            s3_params["key"] = s3_key
        sources["s3"] = s3_params

    # Detect S3 audit source (when audit_key is specified)
    audit_key = annotations.get("audit_key") or annotations.get("auditKey")
    if s3_bucket and audit_key:
        sources["s3_audit"] = {"bucket": s3_bucket, "key": audit_key}

    # Detect S3 processed bucket (for output verification)
    processed_bucket = annotations.get("processed_bucket") or annotations.get("processedBucket")
    processed_prefix = annotations.get("processed_prefix")
    if processed_bucket:
        processed_params: dict[str, str] = {"bucket": processed_bucket}
        if processed_prefix:
            processed_params["prefix"] = processed_prefix
        sources["s3_processed"] = processed_params

    # Detect local file sources
    log_file = (
        annotations.get("log_file") or annotations.get("log_path") or annotations.get("logFile")
    )
    if log_file:
        sources["local_file"] = {"log_file": log_file}

    # Detect Lambda sources
    # Collect all Lambda functions from annotations (primary + upstream/downstream)
    lambda_functions: list[str] = []
    for key in annotations:
        if key in ("function_name", "lambda_function") and annotations[key]:
            # Primary function (prioritize it)
            lambda_functions.insert(0, annotations[key])
        elif (
            key.endswith("_function")
            and annotations[key]
            and annotations[key] not in lambda_functions
        ):
            # Additional functions (ingester_function, mock_dag_function, etc.)
            lambda_functions.append(annotations[key])

    if lambda_functions:
        # Store primary function and additional functions
        sources["lambda"] = {
            "function_name": lambda_functions[0],  # Primary function for single-function actions
            "all_functions": lambda_functions,  # All functions for multi-function investigations
        }

    # Detect Tracer Web sources from context
    tracer_web_run = context.get("tracer_web_run", {})
    if isinstance(tracer_web_run, dict) and tracer_web_run.get("trace_id"):
        tracer_params: dict[str, str] = {"trace_id": tracer_web_run["trace_id"]}
        if tracer_web_run.get("run_url"):
            tracer_params["run_url"] = tracer_web_run["run_url"]
        sources["tracer_web"] = tracer_params

    # Collect ALL AWS-related metadata for dynamic AWS SDK investigations
    aws_metadata: dict[str, Any] = {}

    # Common AWS resource identifiers
    aws_patterns = [
        # ECS/Fargate
        "ecs_cluster",
        "ecs_service",
        "ecs_task",
        "ecs_task_arn",
        "task_definition",
        # RDS
        "db_instance",
        "db_instance_identifier",
        "db_cluster",
        # EC2
        "instance_id",
        "vpc_id",
        "subnet_id",
        "security_group",
        # Lambda (additional metadata beyond function_name)
        "lambda_arn",
        "lambda_alias",
        # S3 (additional buckets)
        "processed_bucket",
        "audit_bucket",
        # Step Functions
        "state_machine_arn",
        "execution_arn",
        # CloudFormation
        "stack_name",
        "stack_id",
        # EKS / Kubernetes
        "eks_cluster",
        "cluster_name",
        "kube_namespace",
        "kubernetes_namespace",
        "pod_name",
        "kube_deployment",
        "node_name",
        # General AWS
        "aws_account_id",
        "aws_region",
        "region",
    ]

    for key, value in annotations.items():
        # Collect fields matching AWS patterns
        if any(pattern in key.lower() for pattern in aws_patterns):
            if value and key not in aws_metadata:
                aws_metadata[key] = value
        # Also collect any field ending with common AWS suffixes
        elif value and any(
            key.endswith(suffix)
            for suffix in ("_arn", "_id", "_name", "_cluster", "_bucket", "_queue", "_topic")
        ):
            aws_metadata[key] = value

    # Add region for AWS SDK calls
    if "region" not in aws_metadata and "aws_region" not in aws_metadata:
        aws_metadata["region"] = (
            annotations.get("cloudwatch_region")
            or annotations.get("aws_region")
            or annotations.get("region")
            or "us-east-1"
        )

    if aws_metadata:
        sources["aws_metadata"] = aws_metadata

    # Detect Grafana sources from resolved_integrations
    common_labels = raw_alert.get("commonLabels", {}) if isinstance(raw_alert, dict) else {}
    pipeline_name = (
        annotations.get("pipeline_name")
        or common_labels.get("pipeline_name")
        or context.get("pipeline_name", "")
    )
    execution_run_id = (
        annotations.get("execution_run_id")
        or annotations.get("executionRunId")
        or annotations.get("correlation_id")
    )
    trace_id = str(
        annotations.get("trace_id")
        or annotations.get("traceId")
        or raw_alert.get("trace_id", "")
        or raw_alert.get("traceId", "")
        or context.get("trace_id", "")
    ).strip()
    service_name = str(
        annotations.get("service_name")
        or annotations.get("service")
        or raw_alert.get("service_name", "")
        or raw_alert.get("service", "")
        or pipeline_name
    ).strip()

    # Only include Grafana when alert came from Grafana, or when source is truly unknown,
    # or when a pre-injected backend is present (e.g. FixtureGrafanaBackend for synthetic tests).
    grafana_int = None
    grafana_local = False
    if resolved_integrations:
        # Multi-instance support: an alert may carry a ``grafana_instance`` hint
        # naming a specific configured instance (e.g. ``"prod"``, ``"staging"``).
        # When the hint matches, select that instance; otherwise fall back to
        # the default (first) instance. See app/integrations/selectors.py.
        grafana_hint: str | None = None
        if isinstance(raw_alert, dict):
            hint_candidate = raw_alert.get("grafana_instance")
            if not hint_candidate:
                nested = raw_alert.get("annotations")
                if isinstance(nested, dict):
                    hint_candidate = nested.get("grafana_instance")
            if hint_candidate:
                grafana_hint = str(hint_candidate).strip().lower() or None

        if grafana_hint:
            from app.integrations.selectors import get_instance_by_name

            selected = get_instance_by_name(resolved_integrations, "grafana", grafana_hint)
            if selected is not None:
                grafana_int = selected
                # The classifier strips api_key to "" for local grafana and
                # rejects cloud grafana with an empty api_key, so an empty
                # api_key in a classified instance uniquely identifies a
                # local instance. Mirror the local flag so downstream gating
                # (loki_only, anonymous-auth acceptance) works the same as
                # the non-hint fallback path.
                if not selected.get("api_key"):
                    grafana_local = True
            else:
                # A hint that does not resolve is almost always a typo or a
                # removed instance — warn so operators notice instead of
                # silently querying the wrong Grafana.
                logger.warning(
                    "grafana_instance hint %r not found; falling back to default instance",
                    grafana_hint,
                )

        if grafana_int is None:
            if resolved_integrations.get("grafana_local"):
                grafana_int = resolved_integrations["grafana_local"]
                grafana_local = True
            elif resolved_integrations.get("grafana"):
                grafana_int = resolved_integrations["grafana"]

    # When a _backend is injected we allow any alert_source; otherwise restrict to Grafana/unknown.
    _has_injected_backend = bool(grafana_int and "_backend" in grafana_int)
    if grafana_int and not (_has_injected_backend or alert_source in ("grafana", "")):
        grafana_int = None  # suppress real Grafana for non-Grafana alerts

    if grafana_int:
        endpoint = grafana_int.get("endpoint", "")
        api_key = grafana_int.get("api_key", "")
        has_backend = "_backend" in grafana_int
        # Local Grafana uses anonymous auth; injected backends don't need credentials at all.
        if has_backend or (endpoint and (api_key or grafana_local)):
            service_name = _map_pipeline_to_service_name(pipeline_name) if pipeline_name else ""

            grafana_params: dict[str, Any] = {
                "service_name": service_name,
                "pipeline_name": pipeline_name,
                "connection_verified": True,
                "grafana_endpoint": endpoint,
                "grafana_api_key": api_key,
                "time_range_minutes": alert_time_range_minutes,
                "loki_only": grafana_local,  # signals no Tempo/Prometheus in local stack
            }
            if execution_run_id:
                grafana_params["execution_run_id"] = execution_run_id
            if has_backend:
                grafana_params["_backend"] = grafana_int["_backend"]
            sources["grafana"] = grafana_params

    # Only include Datadog when alert came from Datadog, or when source is truly unknown,
    # or when a pre-injected backend is present (e.g. FixtureDatadogBackend for synthetic tests).
    dd_int = None
    if resolved_integrations and resolved_integrations.get("datadog"):
        dd_int = resolved_integrations["datadog"]

    _has_injected_dd_backend = bool(dd_int and "_backend" in dd_int)
    if dd_int and not (_has_injected_dd_backend or alert_source in ("datadog", "")):
        dd_int = None  # suppress real Datadog for non-Datadog alerts

    if dd_int:
        dd_api_key = dd_int.get("api_key", "")
        dd_app_key = dd_int.get("app_key", "")

        if _has_injected_dd_backend or (dd_api_key and dd_app_key):
            # kube_namespace: prefer LLM-injected top-level field, fall back to annotations
            kube_namespace = raw_alert.get("kube_namespace", "") or annotations.get(
                "kube_namespace", ""
            )

            # Build a default log query from alert context.
            # Priority:
            #   1. Explicit query field in annotations (e.g., from Datadog monitor webhook body)
            #   2. LLM-extracted log_query or error_message that looks like a Datadog query
            #   3. kube_namespace-scoped query (use * to get all logs, not PIPELINE_ERROR only)
            #   4. pipeline_name as last resort
            alert_query = (
                annotations.get("query", "")
                or annotations.get("log_query", "")
                or raw_alert.get("log_query", "")
            )
            # If the error_message from alert body looks like a Datadog search term, prefer it
            error_message = raw_alert.get("error_message", "")
            if not alert_query and error_message and len(error_message) < 200:
                # Use error keyword + namespace as query (e.g. "OOMKilled kube_namespace:tracer-cl")
                if kube_namespace and " " not in error_message.strip():
                    alert_query = f"{error_message.strip()} kube_namespace:{kube_namespace}"
                elif kube_namespace:
                    alert_query = f"kube_namespace:{kube_namespace}"

            default_query = alert_query or (
                f"kube_namespace:{kube_namespace}" if kube_namespace else pipeline_name or "*"
            )

            dd_params: dict[str, Any] = {
                "api_key": dd_api_key,
                "app_key": dd_app_key,
                "site": dd_int.get("site", "datadoghq.com"),
                "pipeline_name": pipeline_name,
                "default_query": default_query,
                "monitor_query": f"tag:pipeline:{pipeline_name}" if pipeline_name else None,
                "time_range_minutes": alert_time_range_minutes,
            }
            if _has_injected_dd_backend:
                # Backend-only path: only fixture-aware Datadog tools should activate,
                # so connection_verified stays unset to keep the real-Datadog tools quiet.
                dd_params["_backend"] = dd_int["_backend"]
            else:
                dd_params["connection_verified"] = True

            kube_job = annotations.get("kube_job", "") or annotations.get("kube_job_name", "")
            kube_deployment = annotations.get("kube_deployment", "")
            if kube_namespace or kube_job:
                dd_params["kubernetes_context"] = {
                    k: v
                    for k, v in {
                        "namespace": kube_namespace,
                        "job": kube_job,
                        "deployment": kube_deployment,
                    }.items()
                    if v
                }

            sources["datadog"] = dd_params

    if (
        resolved_integrations
        and resolved_integrations.get("honeycomb")
        and alert_source in ("honeycomb", "")
    ):
        honeycomb_int = resolved_integrations["honeycomb"]
        honeycomb_api_key = str(honeycomb_int.get("api_key", "")).strip()
        honeycomb_dataset = str(honeycomb_int.get("dataset", "__all__")).strip() or "__all__"
        honeycomb_base_url = str(honeycomb_int.get("base_url", "https://api.honeycomb.io")).strip()
        if honeycomb_api_key:
            sources["honeycomb"] = {
                "dataset": honeycomb_dataset,
                "service_name": service_name,
                "trace_id": trace_id,
                "error_hint": str(
                    raw_alert.get("error_message", "")
                    or raw_alert.get("alert_name", "")
                    or annotations.get("summary", "")
                ).strip(),
                "honeycomb_api_key": honeycomb_api_key,
                "honeycomb_base_url": honeycomb_base_url,
                "time_range_seconds": alert_time_range_minutes * 60,
                "connection_verified": True,
            }

    if (
        resolved_integrations
        and resolved_integrations.get("coralogix")
        and alert_source in ("coralogix", "")
    ):
        coralogix_int = resolved_integrations["coralogix"]
        coralogix_api_key = str(coralogix_int.get("api_key", "")).strip()
        coralogix_base_url = str(coralogix_int.get("base_url", "https://api.coralogix.com")).strip()
        if coralogix_api_key and coralogix_base_url:
            application_name = str(
                annotations.get("application_name")
                or annotations.get("applicationname")
                or raw_alert.get("application_name", "")
                or raw_alert.get("applicationname", "")
                or coralogix_int.get("application_name", "")
                or pipeline_name
            ).strip()
            subsystem_name = str(
                annotations.get("subsystem_name")
                or annotations.get("subsystemname")
                or raw_alert.get("subsystem_name", "")
                or raw_alert.get("subsystemname", "")
                or coralogix_int.get("subsystem_name", "")
            ).strip()
            raw_query = str(
                annotations.get("query", "")
                or annotations.get("log_query", "")
                or raw_alert.get("log_query", "")
            ).strip()
            text_query = ""
            if not raw_query:
                text_query = str(
                    raw_alert.get("error_message", "")
                    or raw_alert.get("alert_name", "")
                    or annotations.get("summary", "")
                ).strip()

            sources["coralogix"] = {
                "application_name": application_name,
                "subsystem_name": subsystem_name,
                "trace_id": trace_id,
                "default_query": build_coralogix_logs_query(
                    raw_query=raw_query,
                    application_name=application_name,
                    subsystem_name=subsystem_name,
                    text_query=text_query,
                    trace_id=trace_id,
                    limit=50,
                ),
                "coralogix_api_key": coralogix_api_key,
                "coralogix_base_url": coralogix_base_url,
                "time_range_minutes": alert_time_range_minutes,
                "connection_verified": True,
            }

    # Detect EKS: uses the AWS integration (EKS maps to aws in resolve_integrations).
    # A pre-injected _backend (e.g. FixtureEKSBackend for synthetic tests) bypasses
    # the role_arn credential gate the same way the Grafana path does.
    _eks_int = (resolved_integrations or {}).get("aws")
    _has_injected_eks_backend = bool(_eks_int and "_backend" in _eks_int)
    if _eks_int and (_eks_int.get("role_arn") or _has_injected_eks_backend):
        eks_cluster = annotations.get("eks_cluster") or annotations.get("cluster_name")
        # When a backend is injected but the alert omits cluster_name from its
        # annotations, fall back to the first cluster_names entry on the
        # integration dict.  Without this fallback, backend-only investigations
        # silently produce zero EKS tool activity — future synthetic scenarios
        # that forget to put cluster_name in commonAnnotations would otherwise
        # fail with no diagnostic.
        if not eks_cluster and _has_injected_eks_backend:
            cluster_names = _eks_int.get("cluster_names") or []
            if cluster_names:
                eks_cluster = cluster_names[0]
        kube_namespace = (
            annotations.get("kube_namespace")
            or annotations.get("kubernetes_namespace")
            or annotations.get("namespace")
            or "default"
        )

        if eks_cluster:
            eks_params: dict[str, Any] = {
                "cluster_name": eks_cluster,
                "namespace": kube_namespace,
                "pod_name": annotations.get("pod_name", ""),
                "deployment": (
                    annotations.get("deployment") or annotations.get("kube_deployment", "")
                ),
                "node_name": annotations.get("node_name", ""),
                "region": (
                    annotations.get("aws_region")
                    or annotations.get("region")
                    or _eks_int.get("region", "us-east-1")
                ),
                "role_arn": _eks_int.get("role_arn", ""),
                "external_id": _eks_int.get("external_id", ""),
                "cluster_names": _eks_int.get("cluster_names", []),
            }
            if _has_injected_eks_backend:
                # Backend-only path: only fixture-aware EKS tools should activate,
                # so connection_verified stays unset to keep the real-AWS tools quiet.
                eks_params["_backend"] = _eks_int["_backend"]
            else:
                eks_params["connection_verified"] = True
            sources["eks"] = eks_params

    github_int = (resolved_integrations or {}).get("github")
    if github_int:
        repo_url = str(
            annotations.get("repo_url")
            or annotations.get("repository_url")
            or annotations.get("vercel_repo_url")
            or raw_alert.get("repo_url", "")
            or raw_alert.get("vercel_repo_url", "")
        )
        owner = str(
            annotations.get("github_owner")
            or annotations.get("repo_owner")
            or annotations.get("vercel_github_owner")
            or raw_alert.get("github_owner", "")
            or raw_alert.get("vercel_github_owner", "")
        ).strip()
        repo = str(
            annotations.get("github_repo")
            or annotations.get("repo_name")
            or annotations.get("vercel_github_repo_name")
            or raw_alert.get("github_repo", "")
            or raw_alert.get("vercel_github_repo_name", "")
        ).strip()
        full_name = str(
            annotations.get("repository")
            or annotations.get("repo")
            or annotations.get("vercel_github_repo")
            or raw_alert.get("repository", "")
            or raw_alert.get("vercel_github_repo", "")
        ).strip()
        if not owner or not repo:
            owner, repo = _split_repo_full_name(full_name)
        if (not owner or not repo) and repo_url:
            owner, repo = _parse_repo_url(repo_url)

        if owner and repo:
            github_query = str(
                annotations.get("github_query")
                or annotations.get("code_query")
                or raw_alert.get("error_message", "")
                or raw_alert.get("alert_name", "")
            ).strip()
            sources["github"] = {
                "owner": owner,
                "repo": repo,
                "sha": str(
                    annotations.get("commit_sha")
                    or annotations.get("github_sha")
                    or annotations.get("vercel_github_commit_sha")
                    or raw_alert.get("sha", "")
                    or raw_alert.get("vercel_github_commit_sha", "")
                ).strip(),
                "ref": str(
                    annotations.get("branch")
                    or annotations.get("github_ref")
                    or annotations.get("vercel_github_commit_ref")
                    or raw_alert.get("branch", "")
                    or raw_alert.get("vercel_github_commit_ref", "")
                ).strip(),
                "path": str(
                    annotations.get("file_path")
                    or annotations.get("github_path")
                    or raw_alert.get("file_path", "")
                ).strip(),
                "query": github_query or "exception OR error",
                "github_url": str(github_int.get("url", "")).strip(),
                "github_mode": str(github_int.get("mode", "streamable-http")).strip(),
                "github_token": str(github_int.get("auth_token", "")).strip(),
                "github_command": str(github_int.get("command", "")).strip(),
                "github_args": github_int.get("args", []),
                "connection_verified": True,
            }

    openclaw_int = (resolved_integrations or {}).get("openclaw")
    if openclaw_int:
        openclaw_url = str(openclaw_int.get("url", "")).strip()
        openclaw_command = str(openclaw_int.get("command", "")).strip()
        if openclaw_url or openclaw_command:
            openclaw_search_query = str(
                annotations.get("openclaw_search")
                or raw_alert.get("openclaw_search", "")
                or service_name
                or pipeline_name
                or raw_alert.get("alert_name", "")
                or raw_alert.get("title", "")
                or annotations.get("summary", "")
            ).strip()
            sources["openclaw"] = {
                "openclaw_url": openclaw_url,
                "openclaw_mode": str(
                    openclaw_int.get("mode", "streamable-http")
                ).strip()
                or "streamable-http",
                "openclaw_token": str(openclaw_int.get("auth_token", "")).strip(),
                "openclaw_command": openclaw_command,
                "openclaw_args": openclaw_int.get("args", []),
                "openclaw_search_query": openclaw_search_query,
                "connection_verified": True,
            }

    gitlab_int = (resolved_integrations or {}).get("gitlab")
    if gitlab_int:
        repo_url = str(
            annotations.get("repo_url")
            or annotations.get("repository_url")
            or raw_alert.get("repo_url", "")
        )
        project_id = str(
            annotations.get("gitlab_project")
            or annotations.get("gitlab_repo")
            or annotations.get("repository")
            or annotations.get("repo")
            or raw_alert.get("gitlab_project", "")
        ).strip()
        if not project_id and repo_url:
            project_id = _parse_gitlab_repo_url(repo_url)

        if project_id:
            sources["gitlab"] = {
                "project_id": project_id,
                "ref_name": str(
                    annotations.get("branch")
                    or annotations.get("gitlab_ref")
                    or annotations.get("ref_name")
                    or raw_alert.get("branch", "")
                ).strip()
                or "main",
                "file_path": str(
                    annotations.get("file_path")
                    or annotations.get("gitlab_path")
                    or raw_alert.get("file_path", "")
                ).strip(),
                "since": _alert_since_iso(raw_alert),
                "updated_after": _alert_since_iso(raw_alert),
                "gitlab_url": str(gitlab_int.get("base_url", "")).strip(),
                "gitlab_token": str(gitlab_int.get("auth_token", "")).strip(),
                "merge_request_iid": str(annotations.get("mr_iid", "")).strip(),
                "connection_verified": True,
            }
    vercel_int = (resolved_integrations or {}).get("vercel")
    if vercel_int and str(vercel_int.get("api_token", "")).strip():
        sources["vercel"] = {
            "api_token": str(vercel_int.get("api_token", "")).strip(),
            "team_id": str(vercel_int.get("team_id", "")).strip(),
            "project_id": str(
                annotations.get("vercel_project_id") or raw_alert.get("vercel_project_id", "")
            ).strip(),
            "project_name": str(
                annotations.get("vercel_project_name") or raw_alert.get("vercel_project_name", "")
            ).strip(),
            "project_slug": str(
                annotations.get("vercel_project_slug") or raw_alert.get("vercel_project_slug", "")
            ).strip(),
            "deployment_id": str(
                annotations.get("vercel_deployment_id") or raw_alert.get("vercel_deployment_id", "")
            ).strip(),
            "selected_log_id": str(
                annotations.get("vercel_selected_log_id")
                or raw_alert.get("vercel_selected_log_id", "")
            ).strip(),
            "log_url": str(
                annotations.get("vercel_log_url")
                or raw_alert.get("vercel_log_url", "")
                or raw_alert.get("vercel_url", "")
            ).strip(),
            "github_repo": str(
                annotations.get("repository")
                or annotations.get("vercel_github_repo")
                or raw_alert.get("repository", "")
                or raw_alert.get("vercel_github_repo", "")
            ).strip(),
            "github_commit_sha": str(
                annotations.get("github_sha")
                or annotations.get("commit_sha")
                or annotations.get("vercel_github_commit_sha")
                or raw_alert.get("sha", "")
                or raw_alert.get("vercel_github_commit_sha", "")
            ).strip(),
            "github_commit_ref": str(
                annotations.get("github_ref")
                or annotations.get("branch")
                or annotations.get("vercel_github_commit_ref")
                or raw_alert.get("branch", "")
                or raw_alert.get("vercel_github_commit_ref", "")
            ).strip(),
            "error_message": str(
                annotations.get("error") or raw_alert.get("error_message", "")
            ).strip(),
            "connection_verified": True,
        }

    sentry_int = (resolved_integrations or {}).get("sentry")
    if sentry_int:
        issue_id = str(
            annotations.get("sentry_issue_id")
            or annotations.get("issue_id")
            or raw_alert.get("sentry_issue_id", "")
        ).strip()
        if not issue_id:
            issue_id = _extract_issue_id_from_url(
                str(
                    annotations.get("sentry_issue_url")
                    or annotations.get("issue_url")
                    or raw_alert.get("sentry_issue_url", "")
                )
            )

        sentry_query = str(
            annotations.get("sentry_query")
            or raw_alert.get("error_message", "")
            or raw_alert.get("alert_name", "")
        ).strip()
        if issue_id or sentry_query:
            sources["sentry"] = {
                "organization_slug": str(sentry_int.get("organization_slug", "")).strip(),
                "project_slug": str(
                    annotations.get("sentry_project") or sentry_int.get("project_slug", "")
                ).strip(),
                "issue_id": issue_id,
                "query": sentry_query,
                "sentry_url": str(sentry_int.get("base_url", "https://sentry.io")).strip(),
                "sentry_token": str(sentry_int.get("auth_token", "")).strip(),
                "connection_verified": True,
            }

    mongodb_int = (resolved_integrations or {}).get("mongodb")
    if mongodb_int:
        mongodb_connection_string = str(mongodb_int.get("connection_string", "")).strip()
        if mongodb_connection_string:
            mongodb_database = str(
                annotations.get("mongodb_database")
                or annotations.get("database")
                or mongodb_int.get("database", "")
            ).strip()
            mongodb_collection = str(
                annotations.get("mongodb_collection") or annotations.get("collection") or ""
            ).strip()
            sources["mongodb"] = {
                "connection_string": mongodb_connection_string,
                "database": mongodb_database,
                "collection": mongodb_collection,
                "auth_source": str(mongodb_int.get("auth_source", "admin")).strip(),
                "tls": mongodb_int.get("tls", True),
                "connection_verified": True,
            }

    postgresql_int = (resolved_integrations or {}).get("postgresql")
    if postgresql_int:
        postgresql_host = str(postgresql_int.get("host", "")).strip()
        postgresql_database = str(postgresql_int.get("database", "")).strip()
        if postgresql_host and postgresql_database:
            # Check for PostgreSQL-specific annotations first, then fall back to configured values
            postgresql_database = str(
                annotations.get("postgresql_database")
                or annotations.get("database")
                or postgresql_database
            ).strip()
            postgresql_table = str(
                annotations.get("postgresql_table") or annotations.get("table") or ""
            ).strip()
            postgresql_schema = str(
                annotations.get("postgresql_schema") or annotations.get("schema") or "public"
            ).strip()
            sources["postgresql"] = {
                "host": postgresql_host,
                "port": postgresql_int.get("port", 5432),
                "database": postgresql_database,
                "table": postgresql_table,
                "schema": postgresql_schema,
                "connection_verified": True,
            }

    atlas_int = (resolved_integrations or {}).get("mongodb_atlas")
    if atlas_int and str(atlas_int.get("api_public_key", "")).strip():
        atlas_cluster = str(
            annotations.get("atlas_cluster_name") or annotations.get("cluster_name") or ""
        ).strip()
        sources["mongodb_atlas"] = {
            "api_public_key": str(atlas_int.get("api_public_key", "")).strip(),
            "api_private_key": str(atlas_int.get("api_private_key", "")).strip(),
            "project_id": str(atlas_int.get("project_id", "")).strip(),
            "base_url": str(
                atlas_int.get("base_url", "https://cloud.mongodb.com/api/atlas/v2")
            ).strip(),
            "cluster_name": atlas_cluster,
            "connection_verified": True,
        }

    mariadb_int = (resolved_integrations or {}).get("mariadb")
    if mariadb_int and str(mariadb_int.get("host", "")).strip() and str(mariadb_int.get("database", "")).strip():
        sources["mariadb"] = {
            "host": str(mariadb_int.get("host", "")).strip(),
            "port": mariadb_int.get("port", 3306),
            "database": str(mariadb_int.get("database", "")).strip(),
            "username": str(mariadb_int.get("username", "")).strip(),
            "password": str(mariadb_int.get("password", "")).strip(),
            "ssl": mariadb_int.get("ssl", True),
            "connection_verified": True,
        }

    alertmanager_int = (resolved_integrations or {}).get("alertmanager")
    if alertmanager_int and str(alertmanager_int.get("base_url", "")).strip():
        # Carry label filters from the alert when present so tools can pre-scope the query
        filter_labels: list[str] = []
        alertname = str(
            common_labels.get("alertname") or annotations.get("alertname") or ""
        ).strip()
        if alertname:
            escaped = alertname.replace("\\", "\\\\").replace('"', '\\"')
            filter_labels.append(f'alertname="{escaped}"')
        sources["alertmanager"] = {
            "base_url": str(alertmanager_int.get("base_url", "")).strip().rstrip("/"),
            "bearer_token": str(alertmanager_int.get("bearer_token", "")).strip(),
            "username": str(alertmanager_int.get("username", "")).strip(),
            "password": str(alertmanager_int.get("password", "")).strip(),
            "filter_labels": filter_labels,
            "connection_verified": True,
        }

    opsgenie_int = (resolved_integrations or {}).get("opsgenie")
    if opsgenie_int and str(opsgenie_int.get("api_key", "")).strip():
        alert_id = str(
            annotations.get("opsgenie_alert_id") or raw_alert.get("opsgenie_alert_id", "")
        ).strip()
        opsgenie_query = str(
            annotations.get("opsgenie_query") or raw_alert.get("alert_name", "")
        ).strip()
        sources["opsgenie"] = {
            "api_key": str(opsgenie_int.get("api_key", "")).strip(),
            "region": str(opsgenie_int.get("region", "us")).strip(),
            "alert_id": alert_id,
            "query": opsgenie_query,
            "connection_verified": True,
        }

    jira_int = (resolved_integrations or {}).get("jira")
    if (
        jira_int
        and str(jira_int.get("base_url", "")).strip()
        and str(jira_int.get("email", "")).strip()
        and str(jira_int.get("api_token", "")).strip()
    ):
        sources["jira"] = {
            "base_url": str(jira_int.get("base_url", "")).strip(),
            "email": str(jira_int.get("email", "")).strip(),
            "api_token": str(jira_int.get("api_token", "")).strip(),
            "project_key": str(jira_int.get("project_key", "")).strip(),
            "connection_verified": True,
        }

    mysql_int = (resolved_integrations or {}).get("mysql")
    if mysql_int and str(mysql_int.get("host", "")).strip() and str(mysql_int.get("database", "")).strip():
        mysql_host = str(mysql_int.get("host", "")).strip()
        mysql_database = str(mysql_int.get("database", "")).strip()
        mysql_database = str(
            annotations.get("mysql_database")
            or annotations.get("database")
            or mysql_database
        ).strip()
        mysql_table = str(
            annotations.get("mysql_table") or annotations.get("table") or ""
        ).strip()
        sources["mysql"] = {
            "host": mysql_host,
            "port": mysql_int.get("port", 3306),
            "database": mysql_database,
            "table": mysql_table,
            "connection_verified": True,
        }

    return sources
