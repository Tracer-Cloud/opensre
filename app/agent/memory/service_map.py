"""Service map builder - tracks discovered assets and edges across investigations."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict, cast

from app.agent.memory.io import get_memories_dir
from app.agent.memory.service_map_config import is_service_map_enabled
from app.agent.nodes.publish_findings.formatters.infrastructure import (
    extract_infrastructure_assets,
)


class Asset(TypedDict, total=False):
    """Service map asset with AWS-native ID and metadata."""

    id: str
    type: str
    name: str
    aws_arn: str | None
    pipeline_context: list[str]
    alert_context: list[str]
    investigation_count: int
    last_investigated: str
    confidence: float
    verification_status: str
    metadata: dict[str, Any]


class Edge(TypedDict, total=False):
    """Directed edge between assets."""

    from_asset: str
    to_asset: str
    type: str
    confidence: float
    verification_status: str
    evidence: str
    first_seen: str
    last_seen: str


class HistoryEntry(TypedDict):
    """Change history entry."""

    timestamp: str
    change_type: str
    asset_id: str | None
    edge_id: str | None
    details: str


class ServiceMap(TypedDict):
    """Complete service map snapshot."""

    enabled: bool
    last_updated: str
    assets: list[Asset]
    edges: list[Edge]
    history: list[HistoryEntry]


def _generate_asset_id(asset_type: str, name: str) -> str:
    """Generate stable asset ID from type and name."""
    clean_name = name.replace(":", "_").replace("/", "_")
    return f"{asset_type}:{clean_name}"


def _extract_assets_from_infrastructure(
    ctx: dict[str, Any], pipeline_name: str, alert_name: str
) -> list[Asset]:
    """Extract assets from infrastructure extraction."""
    from app.agent.nodes.publish_findings.context.models import ReportContext

    report_ctx: ReportContext = {
        "pipeline_name": pipeline_name,
        "root_cause": "",
        "confidence": 0.0,
        "validated_claims": [],
        "non_validated_claims": [],
        "validity_score": 0.0,
        "s3_marker_exists": False,
        "tracer_run_status": None,
        "tracer_run_name": None,
        "tracer_pipeline_name": None,
        "tracer_run_cost": 0.0,
        "tracer_max_ram_gb": 0.0,
        "tracer_user_email": None,
        "tracer_team": None,
        "tracer_instance_type": None,
        "tracer_failed_tasks": 0,
        "batch_failure_reason": None,
        "batch_failed_jobs": 0,
        "cloudwatch_log_group": ctx.get("raw_alert", {}).get("cloudwatch_log_group"),
        "cloudwatch_log_stream": ctx.get("raw_alert", {}).get("cloudwatch_log_stream"),
        "cloudwatch_logs_url": ctx.get("raw_alert", {}).get("cloudwatch_logs_url"),
        "cloudwatch_region": ctx.get("raw_alert", {}).get("cloudwatch_region"),
        "alert_id": ctx.get("raw_alert", {}).get("alert_id"),
        "evidence": ctx.get("evidence", {}),
        "raw_alert": ctx.get("raw_alert", {}),
    }

    infra_assets = extract_infrastructure_assets(report_ctx)
    assets: list[Asset] = []
    now = datetime.now(UTC).isoformat()

    # API Gateway
    if infra_assets.get("api_gateway"):
        assets.append(
            {
                "id": _generate_asset_id("api_gateway", infra_assets["api_gateway"]),
                "type": "api_gateway",
                "name": infra_assets["api_gateway"],
                "aws_arn": None,
                "pipeline_context": [pipeline_name] if pipeline_name else [],
                "alert_context": [alert_name] if alert_name else [],
                "investigation_count": 1,
                "last_investigated": now,
                "confidence": 1.0,
                "verification_status": "verified",
                "metadata": {},
            }
        )

    # Lambda functions
    lambda_assets_seen = set()
    for lambda_func in infra_assets.get("lambda_functions", []):
        func_name = lambda_func["name"]
        role = lambda_func.get("role", "")

        # Override role from annotations if this is marked as trigger lambda
        raw_alert = ctx.get("raw_alert", {})
        annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {})
        trigger_lambda = annotations.get("trigger_lambda") or annotations.get("ingestion_lambda")
        if trigger_lambda and func_name == trigger_lambda:
            role = "trigger"

        lambda_id = _generate_asset_id("lambda", func_name)
        lambda_assets_seen.add(lambda_id)
        assets.append(
            {
                "id": lambda_id,
                "type": "lambda",
                "name": func_name,
                "aws_arn": None,
                "pipeline_context": [pipeline_name] if pipeline_name else [],
                "alert_context": [alert_name] if alert_name else [],
                "investigation_count": 1,
                "last_investigated": now,
                "confidence": 1.0,
                "verification_status": "verified",
                "metadata": {"role": role, "runtime": lambda_func.get("runtime")},
            }
        )

    # Fallback: extract Lambda from evidence if not found via infrastructure
    evidence = ctx.get("evidence", {})
    lambda_function = evidence.get("lambda_function", {})
    if lambda_function and lambda_function.get("function_name"):
        func_name = lambda_function["function_name"]
        lambda_id = _generate_asset_id("lambda", func_name)
        if lambda_id not in lambda_assets_seen:
            # Check if this is a trigger lambda
            raw_alert = ctx.get("raw_alert", {})
            annotations = (
                raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {})
            )
            trigger_lambda = (
                annotations.get("trigger_lambda") or annotations.get("ingestion_lambda")
            )
            role = "trigger" if func_name == trigger_lambda else "primary"

            assets.append(
                {
                    "id": lambda_id,
                    "type": "lambda",
                    "name": func_name,
                    "aws_arn": None,
                    "pipeline_context": [pipeline_name] if pipeline_name else [],
                    "alert_context": [alert_name] if alert_name else [],
                    "investigation_count": 1,
                    "last_investigated": now,
                    "confidence": 1.0,
                    "verification_status": "verified",
                    "metadata": {"role": role, "runtime": lambda_function.get("runtime")},
                }
            )

    # S3 buckets - deduplicate by bucket name
    s3_buckets_seen: dict[str, Asset] = {}
    for s3_bucket in infra_assets.get("s3_buckets", []):
        bucket_name = s3_bucket["name"]
        bucket_id = _generate_asset_id("s3_bucket", bucket_name)

        # Merge keys if same bucket seen multiple times
        if bucket_id in s3_buckets_seen:
            existing_asset = s3_buckets_seen[bucket_id]
            # Append key to list
            existing_keys = existing_asset["metadata"].get("keys", [])
            new_key = s3_bucket.get("key")
            if new_key and new_key not in existing_keys:
                existing_keys.append(new_key)
            existing_asset["metadata"]["keys"] = existing_keys
            # Update bucket type if more specific
            if s3_bucket.get("type"):
                existing_asset["metadata"]["bucket_type"] = s3_bucket.get("type")
        else:
            # New bucket
            asset: Asset = {
                "id": bucket_id,
                "type": "s3_bucket",
                "name": bucket_name,
                "aws_arn": None,
                "pipeline_context": [pipeline_name] if pipeline_name else [],
                "alert_context": [alert_name] if alert_name else [],
                "investigation_count": 1,
                "last_investigated": now,
                "confidence": 1.0,
                "verification_status": "verified",
                "metadata": {
                    "bucket_type": s3_bucket.get("type"),
                    "keys": [s3_bucket.get("key")] if s3_bucket.get("key") else [],
                },
            }
            s3_buckets_seen[bucket_id] = asset
            assets.append(asset)

    # ECS service
    if infra_assets.get("ecs_service"):
        ecs = infra_assets["ecs_service"]
        cluster = ecs.get("cluster")
        flow_name = ecs.get("flow_name")
        if cluster:
            assets.append(
                {
                    "id": _generate_asset_id("ecs_cluster", cluster),
                    "type": "ecs_cluster",
                    "name": cluster,
                    "aws_arn": None,
                    "pipeline_context": [pipeline_name] if pipeline_name else [],
                    "alert_context": [alert_name] if alert_name else [],
                    "investigation_count": 1,
                    "last_investigated": now,
                    "confidence": 1.0,
                    "verification_status": "verified",
                    "metadata": {"flow_name": flow_name, "task_arn": ecs.get("task")},
                }
            )

    # Batch service
    if infra_assets.get("batch_service"):
        batch = infra_assets["batch_service"]
        queue = batch.get("queue")
        if queue:
            assets.append(
                {
                    "id": _generate_asset_id("batch_queue", queue),
                    "type": "batch_queue",
                    "name": queue,
                    "aws_arn": None,
                    "pipeline_context": [pipeline_name] if pipeline_name else [],
                    "alert_context": [alert_name] if alert_name else [],
                    "investigation_count": 1,
                    "last_investigated": now,
                    "confidence": 1.0,
                    "verification_status": "verified",
                    "metadata": {"job_definition": batch.get("definition")},
                }
            )

    # CloudWatch log groups
    for log_group in infra_assets.get("log_groups", []):
        lg_name = log_group["name"]
        assets.append(
            {
                "id": _generate_asset_id("cloudwatch_log_group", lg_name),
                "type": "cloudwatch_log_group",
                "name": lg_name,
                "aws_arn": None,
                "pipeline_context": [pipeline_name] if pipeline_name else [],
                "alert_context": [alert_name] if alert_name else [],
                "investigation_count": 1,
                "last_investigated": now,
                "confidence": 1.0,
                "verification_status": "verified",
                "metadata": {"log_type": log_group.get("type")},
            }
        )

    # Pipeline
    if infra_assets.get("pipeline"):
        assets.append(
            {
                "id": _generate_asset_id("pipeline", infra_assets["pipeline"]),
                "type": "pipeline",
                "name": infra_assets["pipeline"],
                "aws_arn": None,
                "pipeline_context": [pipeline_name] if pipeline_name else [],
                "alert_context": [alert_name] if alert_name else [],
                "investigation_count": 1,
                "last_investigated": now,
                "confidence": 1.0,
                "verification_status": "verified",
                "metadata": {},
            }
        )

    return assets


def _extract_s3_metadata_edges(evidence: dict[str, Any]) -> list[Edge]:
    """Extract edges directly from S3 metadata fields."""
    edges: list[Edge] = []
    now = datetime.now(UTC).isoformat()

    s3_obj = evidence.get("s3_object", {})
    if not s3_obj.get("found"):
        return edges

    bucket = s3_obj.get("bucket", "")
    metadata = s3_obj.get("metadata", {})

    # Lambda → S3 edge (from metadata.source)
    if metadata.get("source"):
        source = metadata["source"]
        edges.append(
            {
                "from_asset": _generate_asset_id("lambda", source),
                "to_asset": _generate_asset_id("s3_bucket", bucket),
                "type": "writes_to",
                "confidence": 1.0,
                "verification_status": "verified",
                "evidence": "s3_metadata.source",
                "first_seen": now,
                "last_seen": now,
            }
        )

    return edges


def _extract_audit_payload_edges(
    evidence: dict[str, Any], raw_alert: dict[str, Any] | None = None
) -> list[Edge]:
    """Extract External API → Lambda edges from audit payload."""
    edges: list[Edge] = []
    now = datetime.now(UTC).isoformat()

    audit_payload = evidence.get("s3_audit_payload", {})
    if not audit_payload.get("found"):
        return edges

    # Parse audit content
    audit_content = audit_payload.get("content", {})
    if isinstance(audit_content, str):
        try:
            audit_content = json.loads(audit_content)
        except json.JSONDecodeError:
            return edges

    if not isinstance(audit_content, dict):
        return edges

    # External API → Lambda edge
    external_api_url = audit_content.get("external_api_url")
    if not external_api_url:
        return edges

    # Determine Lambda name from annotations or correlation_id
    lambda_name = None

    # First, try to get trigger lambda from annotations
    if raw_alert:
        annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {})
        lambda_name = annotations.get("trigger_lambda") or annotations.get("ingestion_lambda")

    # Fallback: infer from correlation_id pattern
    if not lambda_name:
        correlation_id = audit_content.get("correlation_id", "")
        if "trigger" in correlation_id:
            lambda_name = "trigger_lambda"
        elif "direct" in correlation_id:
            lambda_name = "direct_lambda"
        else:
            lambda_name = "ingestion_lambda"

    if lambda_name:
        edges.append(
            {
                "from_asset": "external_api:vendor",
                "to_asset": _generate_asset_id("lambda", lambda_name),
                "type": "triggers",
                "confidence": 0.9,
                "verification_status": "verified",
                "evidence": "audit_payload.external_api_url",
                "first_seen": now,
                "last_seen": now,
            }
        )

    return edges


def _extract_lambda_config_edges(evidence: dict[str, Any]) -> list[Edge]:
    """Extract Lambda → S3 edges from Lambda environment variables."""
    edges: list[Edge] = []
    now = datetime.now(UTC).isoformat()

    lambda_func = evidence.get("lambda_function", {})
    lambda_config = evidence.get("lambda_config", {})

    # Use lambda_function or lambda_config
    lambda_data = lambda_func or lambda_config
    if not lambda_data.get("function_name"):
        return edges

    function_name = lambda_data["function_name"]
    env_vars = lambda_data.get("environment_variables", {})

    # Lambda → S3 edges from environment variables
    s3_bucket_keys = ["S3_BUCKET", "LANDING_BUCKET", "OUTPUT_BUCKET", "BUCKET_NAME"]
    for key in s3_bucket_keys:
        if key in env_vars:
            bucket_name = env_vars[key]
            edges.append(
                {
                    "from_asset": _generate_asset_id("lambda", function_name),
                    "to_asset": _generate_asset_id("s3_bucket", bucket_name),
                    "type": "writes_to",
                    "confidence": 0.9,
                    "verification_status": "verified",
                    "evidence": f"lambda_config.env.{key}",
                    "first_seen": now,
                    "last_seen": now,
                }
            )

    return edges


def _extract_grafana_edges(
    evidence: dict[str, Any], raw_alert: dict[str, Any], pipeline_name: str  # noqa: ARG001
) -> list[Edge]:
    """Extract Pipeline → Grafana datasource edges from OTLP configuration."""
    edges: list[Edge] = []
    now = datetime.now(UTC).isoformat()

    # Check Lambda configuration for OTLP endpoint
    lambda_data = evidence.get("lambda_function", {}) or evidence.get("lambda_config", {})
    if lambda_data.get("function_name"):
        env_vars = lambda_data.get("environment_variables", {})

        otlp_endpoint = env_vars.get("OTEL_EXPORTER_OTLP_ENDPOINT") or env_vars.get(
            "GCLOUD_OTLP_ENDPOINT"
        )

        if otlp_endpoint and "grafana.net" in otlp_endpoint:
            function_name = lambda_data["function_name"]

            edges.append(
                {
                    "from_asset": _generate_asset_id("lambda", function_name),
                    "to_asset": "grafana_datasource:tracerbio",
                    "type": "exports_telemetry_to",
                    "confidence": 1.0,
                    "verification_status": "verified",
                    "evidence": f"OTEL_EXPORTER_OTLP_ENDPOINT={otlp_endpoint}",
                    "first_seen": now,
                    "last_seen": now,
                }
            )

            if pipeline_name:
                edges.append(
                    {
                        "from_asset": _generate_asset_id("pipeline", pipeline_name),
                        "to_asset": "grafana_datasource:tracerbio",
                        "type": "exports_telemetry_to",
                        "confidence": 0.9,
                        "verification_status": "verified",
                        "evidence": f"lambda.{function_name}.OTLP→Grafana",
                        "first_seen": now,
                        "last_seen": now,
                    }
                )

    # Check ECS task definition for OTLP endpoint
    ecs_task = evidence.get("ecs_task_definition", {})
    if ecs_task.get("taskDefinitionArn"):
        for container in ecs_task.get("containerDefinitions", []):
            env_vars = {e["name"]: e["value"] for e in container.get("environment", [])}

            otlp_endpoint = env_vars.get("OTEL_EXPORTER_OTLP_ENDPOINT") or env_vars.get(
                "GCLOUD_OTLP_ENDPOINT"
            )

            if otlp_endpoint and "grafana.net" in otlp_endpoint:
                ecs_cluster = evidence.get("ecs_cluster", {}).get("clusterName", "")
                container_name = container.get("name", "")

                if ecs_cluster:
                    edges.append(
                        {
                            "from_asset": _generate_asset_id("ecs_cluster", ecs_cluster),
                            "to_asset": "grafana_datasource:tracerbio",
                            "type": "exports_telemetry_to",
                            "confidence": 1.0,
                            "verification_status": "verified",
                            "evidence": f"ECS.{container_name}.OTLP→Grafana",
                            "first_seen": now,
                            "last_seen": now,
                        }
                    )

                if pipeline_name:
                    edges.append(
                        {
                            "from_asset": _generate_asset_id("pipeline", pipeline_name),
                            "to_asset": "grafana_datasource:tracerbio",
                            "type": "exports_telemetry_to",
                            "confidence": 0.9,
                            "verification_status": "verified",
                            "evidence": f"ECS.{ecs_cluster}.OTLP→Grafana",
                            "first_seen": now,
                            "last_seen": now,
                        }
                    )
                break

    return edges


def _infer_edges_from_evidence(
    assets: list[Asset], evidence: dict[str, Any], raw_alert: dict[str, Any]
) -> list[Edge]:
    """Infer directed edges from evidence and alert context."""
    edges: list[Edge] = []
    now = datetime.now(UTC).isoformat()

    # First: Extract edges directly from evidence fields (high confidence)
    edges.extend(_extract_s3_metadata_edges(evidence))
    edges.extend(_extract_audit_payload_edges(evidence, raw_alert))
    edges.extend(_extract_lambda_config_edges(evidence))

    # Extract annotations
    annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {})
    if not annotations and raw_alert.get("alerts"):
        first_alert = raw_alert.get("alerts", [{}])[0]
        if isinstance(first_alert, dict):
            annotations = first_alert.get("annotations", {}) or {}

    # Note: External API → Lambda edges now handled by _extract_audit_payload_edges()
    # This creates the external_api asset if audit evidence exists

    # Infer Pipeline → ECS/Batch edges
    pipeline_assets = [a for a in assets if a["type"] == "pipeline"]
    ecs_assets = [a for a in assets if a["type"] == "ecs_cluster"]
    batch_assets = [a for a in assets if a["type"] == "batch_queue"]

    for pipeline_asset in pipeline_assets:
        for ecs_asset in ecs_assets:
            edges.append(
                {
                    "from_asset": pipeline_asset["id"],
                    "to_asset": ecs_asset["id"],
                    "type": "runs_on",
                    "confidence": 1.0,
                    "verification_status": "verified",
                    "evidence": "alert_annotations.ecs_cluster",
                    "first_seen": now,
                    "last_seen": now,
                }
            )
        for batch_asset in batch_assets:
            edges.append(
                {
                    "from_asset": pipeline_asset["id"],
                    "to_asset": batch_asset["id"],
                    "type": "runs_on",
                    "confidence": 1.0,
                    "verification_status": "verified",
                    "evidence": "alert_annotations.batch_queue",
                    "first_seen": now,
                    "last_seen": now,
                }
            )

    # Infer CloudWatch log group associations
    lambda_assets = [a for a in assets if a["type"] == "lambda"]
    log_groups = [a for a in assets if a["type"] == "cloudwatch_log_group"]
    for log_group in log_groups:
        # Associate with Lambda if log group name contains lambda function name
        for lambda_asset in lambda_assets:
            if lambda_asset["name"] in log_group["name"]:
                edges.append(
                    {
                        "from_asset": lambda_asset["id"],
                        "to_asset": log_group["id"],
                        "type": "logs_to",
                        "confidence": 1.0,
                        "verification_status": "verified",
                        "evidence": "log_group_name_pattern",
                        "first_seen": now,
                        "last_seen": now,
                    }
                )

        # Associate with ECS/Pipeline if log group name contains cluster/flow
        for ecs_asset in ecs_assets:
            flow_name = ecs_asset.get("metadata", {}).get("flow_name", "")
            if flow_name and flow_name.lower() in log_group["name"].lower():
                edges.append(
                    {
                        "from_asset": ecs_asset["id"],
                        "to_asset": log_group["id"],
                        "type": "logs_to",
                        "confidence": 0.9,
                        "verification_status": "verified",
                        "evidence": "log_group_name_pattern",
                        "first_seen": now,
                        "last_seen": now,
                    }
                )

    return edges


def _infer_tentative_assets_from_alert(
    alert_name: str, raw_alert: dict[str, Any], existing_assets: list[Asset]
) -> tuple[list[Asset], list[Edge]]:
    """Infer tentative assets and edges from alert text when only partial evidence exists."""
    tentative_assets: list[Asset] = []
    tentative_edges: list[Edge] = []
    now = datetime.now(UTC).isoformat()

    # Build existing asset lookup
    existing_ids = {asset["id"] for asset in existing_assets}

    # Extract alert text sources
    alert_text = alert_name.lower()
    annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {})
    if annotations:
        for _key, value in annotations.items():
            if isinstance(value, str):
                alert_text += " " + value.lower()

    # Pattern: "Lambda timeout writing to S3"
    lambda_s3_pattern = r"lambda.*(?:timeout|error|fail).*(?:writing|write|upload|put).*s3"
    if re.search(lambda_s3_pattern, alert_text, re.IGNORECASE):
        # Check if we have Lambda but missing S3
        lambda_assets = [a for a in existing_assets if a["type"] == "lambda"]
        s3_assets = [a for a in existing_assets if a["type"] == "s3_bucket"]

        if lambda_assets and not s3_assets:
            # Create tentative S3 asset
            tentative_s3_id = "s3_bucket:tentative_destination"
            if tentative_s3_id not in existing_ids:
                tentative_assets.append(
                    {
                        "id": tentative_s3_id,
                        "type": "s3_bucket",
                        "name": "tentative_destination",
                        "aws_arn": None,
                        "pipeline_context": [],
                        "alert_context": [alert_name],
                        "investigation_count": 1,
                        "last_investigated": now,
                        "confidence": 0.6,
                        "verification_status": "needs_verification",
                        "metadata": {"inferred_from": "alert_text"},
                    }
                )
                existing_ids.add(tentative_s3_id)

                # Create tentative edge: Lambda → S3
                for lambda_asset in lambda_assets:
                    tentative_edges.append(
                        {
                            "from_asset": lambda_asset["id"],
                            "to_asset": tentative_s3_id,
                            "type": "writes_to",
                            "confidence": 0.7,
                            "verification_status": "needs_verification",
                            "evidence": "alert_text",
                            "first_seen": now,
                            "last_seen": now,
                        }
                    )

    # Pattern: "S3 object missing" or "file not found"
    s3_missing_pattern = r"s3.*(?:missing|not found|does not exist)|file.*not found"
    if re.search(s3_missing_pattern, alert_text, re.IGNORECASE):
        s3_assets = [a for a in existing_assets if a["type"] == "s3_bucket"]
        if not s3_assets:
            # Create tentative S3 asset
            tentative_s3_id = "s3_bucket:tentative_missing"
            if tentative_s3_id not in existing_ids:
                tentative_assets.append(
                    {
                        "id": tentative_s3_id,
                        "type": "s3_bucket",
                        "name": "tentative_missing",
                        "aws_arn": None,
                        "pipeline_context": [],
                        "alert_context": [alert_name],
                        "investigation_count": 1,
                        "last_investigated": now,
                        "confidence": 0.6,
                        "verification_status": "needs_verification",
                        "metadata": {"inferred_from": "alert_text"},
                    }
                )

    return tentative_assets, tentative_edges


def _merge_with_existing_map(
    new_map: ServiceMap, pipeline_name: str, alert_name: str
) -> ServiceMap:
    """Merge new assets/edges with existing service map, updating hotspots and history."""
    existing_map = load_service_map()
    if not existing_map["enabled"]:
        return new_map

    # Build lookups
    existing_assets_by_id = {asset["id"]: asset for asset in existing_map["assets"]}
    existing_edges_by_key = {
        (edge["from_asset"], edge["to_asset"], edge["type"]): edge
        for edge in existing_map["edges"]
    }

    merged_assets: list[Asset] = []
    merged_edges: list[Edge] = []
    history: list[HistoryEntry] = list(existing_map.get("history", []))
    now = datetime.now(UTC).isoformat()

    # Merge assets (update hotspots)
    for new_asset in new_map["assets"]:
        asset_id = new_asset["id"]
        if asset_id in existing_assets_by_id:
            # Update existing asset
            existing_asset = existing_assets_by_id[asset_id]
            existing_asset["investigation_count"] = existing_asset.get("investigation_count", 0) + 1
            existing_asset["last_investigated"] = now

            # Merge pipeline context
            if pipeline_name and pipeline_name not in existing_asset.get("pipeline_context", []):
                existing_asset.setdefault("pipeline_context", []).append(pipeline_name)

            # Merge alert context
            if alert_name and alert_name not in existing_asset.get("alert_context", []):
                existing_asset.setdefault("alert_context", []).append(alert_name)

            # Upgrade confidence if tentative becomes verified
            if (
                existing_asset.get("verification_status") == "needs_verification"
                and new_asset.get("verification_status") == "verified"
            ):
                existing_asset["verification_status"] = "verified"
                existing_asset["confidence"] = new_asset["confidence"]
                history.append(
                    {
                        "timestamp": now,
                        "change_type": "asset_verified",
                        "asset_id": asset_id,
                        "edge_id": None,
                        "details": f"Asset {asset_id} verified",
                    }
                )

            merged_assets.append(existing_asset)
            existing_assets_by_id.pop(asset_id)
        else:
            # New asset
            merged_assets.append(new_asset)
            history.append(
                {
                    "timestamp": now,
                    "change_type": "asset_added",
                    "asset_id": asset_id,
                    "edge_id": None,
                    "details": f"New asset: {new_asset['type']} {new_asset['name']}",
                }
            )

    # Add remaining existing assets
    merged_assets.extend(existing_assets_by_id.values())

    # Merge edges
    for new_edge in new_map["edges"]:
        edge_key = (new_edge["from_asset"], new_edge["to_asset"], new_edge["type"])
        if edge_key in existing_edges_by_key:
            # Update existing edge
            existing_edge = existing_edges_by_key[edge_key]
            existing_edge["last_seen"] = now

            # Upgrade confidence if tentative becomes verified
            if (
                existing_edge.get("verification_status") == "needs_verification"
                and new_edge.get("verification_status") == "verified"
            ):
                existing_edge["verification_status"] = "verified"
                existing_edge["confidence"] = new_edge["confidence"]
                edge_id = f"{edge_key[0]}→{edge_key[1]}"
                history.append(
                    {
                        "timestamp": now,
                        "change_type": "edge_verified",
                        "asset_id": None,
                        "edge_id": edge_id,
                        "details": f"Edge {edge_id} verified",
                    }
                )

            merged_edges.append(existing_edge)
            existing_edges_by_key.pop(edge_key)
        else:
            # New edge
            merged_edges.append(new_edge)
            edge_id = f"{new_edge['from_asset']}→{new_edge['to_asset']}"
            history.append(
                {
                    "timestamp": now,
                    "change_type": "edge_added",
                    "asset_id": None,
                    "edge_id": edge_id,
                    "details": f"New edge: {new_edge['type']}",
                }
            )

    # Add remaining existing edges
    merged_edges.extend(existing_edges_by_key.values())

    # Keep only last 20 history entries
    history = history[-20:]

    return {
        "enabled": True,
        "last_updated": now,
        "assets": merged_assets,
        "edges": merged_edges,
        "history": history,
    }


def _ensure_edge_endpoint_assets(
    edges: list[Edge], evidence: dict[str, Any], pipeline_name: str, alert_name: str
) -> list[Asset]:
    """Ensure all edge endpoints exist as assets (create if missing)."""
    assets: list[Asset] = []
    now = datetime.now(UTC).isoformat()
    seen_ids = set()

    for edge in edges:
        for asset_id in [edge["from_asset"], edge["to_asset"]]:
            if asset_id in seen_ids:
                continue
            seen_ids.add(asset_id)

            # Parse asset type and name from ID
            asset_type, asset_name = asset_id.split(":", 1)

            # Special handling for external_api: extract URL from audit payload
            if asset_type == "external_api":
                audit_payload = evidence.get("s3_audit_payload", {})
                audit_content = audit_payload.get("content", {})
                if isinstance(audit_content, str):
                    try:
                        audit_content = json.loads(audit_content)
                    except json.JSONDecodeError:
                        audit_content = {}

                api_url = audit_content.get("external_api_url", "unknown")
                assets.append(
                    {
                        "id": asset_id,
                        "type": asset_type,
                        "name": api_url,
                        "aws_arn": None,
                        "pipeline_context": [],
                        "alert_context": [],
                        "investigation_count": 1,
                        "last_investigated": now,
                        "confidence": 0.8,
                        "verification_status": "verified",
                        "metadata": {"inferred_from": "audit_payload"},
                    }
                )
            else:
                # Create minimal asset for other types
                assets.append(
                    {
                        "id": asset_id,
                        "type": asset_type,
                        "name": asset_name,
                        "aws_arn": None,
                        "pipeline_context": [pipeline_name] if pipeline_name else [],
                        "alert_context": [alert_name] if alert_name else [],
                        "investigation_count": 1,
                        "last_investigated": now,
                        "confidence": 0.9,
                        "verification_status": "verified",
                        "metadata": {"created_from": "edge_endpoint"},
                    }
                )

    return assets


def build_service_map(
    evidence: dict[str, Any],
    raw_alert: dict[str, Any],
    context: dict[str, Any],
    pipeline_name: str,
    alert_name: str,
) -> ServiceMap:
    """Build service map from investigation evidence and alert data.

    Args:
        evidence: Investigation evidence dictionary
        raw_alert: Raw alert payload
        context: Investigation context
        pipeline_name: Pipeline name
        alert_name: Alert name

    Returns:
        ServiceMap with assets, edges, and history
    """
    if not is_service_map_enabled():
        return {
            "enabled": False,
            "last_updated": datetime.now(UTC).isoformat(),
            "assets": [],
            "edges": [],
            "history": [],
        }

    # Step 1: Extract edges directly from evidence (evidence-first approach)
    edges_from_evidence = []
    edges_from_evidence.extend(_extract_s3_metadata_edges(evidence))
    edges_from_evidence.extend(_extract_audit_payload_edges(evidence, raw_alert))
    edges_from_evidence.extend(_extract_lambda_config_edges(evidence))
    edges_from_evidence.extend(_extract_grafana_edges(evidence, raw_alert, pipeline_name))

    # Step 2: Ensure edge endpoints exist as assets
    assets_from_edges = _ensure_edge_endpoint_assets(
        edges_from_evidence, evidence, pipeline_name, alert_name
    )

    # Step 3: Extract assets from infrastructure (adds remaining assets + enriches metadata)
    ctx_for_extraction = {
        "evidence": evidence,
        "raw_alert": raw_alert,
        "context": context,
    }
    assets_from_infra = _extract_assets_from_infrastructure(
        ctx_for_extraction, pipeline_name, alert_name
    )

    # Merge assets (prefer infrastructure extraction for metadata)
    assets_by_id = {a["id"]: a for a in assets_from_infra}
    for edge_asset in assets_from_edges:
        if edge_asset["id"] not in assets_by_id:
            assets_by_id[edge_asset["id"]] = edge_asset

    assets = list(assets_by_id.values())

    # Step 4: Infer additional edges from asset topology
    edges_from_topology = _infer_edges_from_evidence(assets, evidence, raw_alert)

    # Combine all edges (deduplicate by key)
    all_edges = edges_from_evidence + edges_from_topology
    edges_by_key = {}
    for edge in all_edges:
        key = (edge["from_asset"], edge["to_asset"], edge["type"])
        if key not in edges_by_key:
            edges_by_key[key] = edge
        else:
            # Prefer higher confidence
            if edge["confidence"] > edges_by_key[key]["confidence"]:
                edges_by_key[key] = edge

    edges = list(edges_by_key.values())

    # Step 5: Infer tentative assets/edges from alert context
    tentative_assets, tentative_edges = _infer_tentative_assets_from_alert(
        alert_name, raw_alert, assets
    )
    assets.extend(tentative_assets)
    edges.extend(tentative_edges)

    # Build new map
    new_map: ServiceMap = {
        "enabled": True,
        "last_updated": datetime.now(UTC).isoformat(),
        "assets": assets,
        "edges": edges,
        "history": [],
    }

    # Merge with existing map (hotspots + history)
    merged_map = _merge_with_existing_map(new_map, pipeline_name, alert_name)

    return merged_map


def load_service_map() -> ServiceMap:
    """Load existing service map from disk.

    Returns:
        ServiceMap or empty map if not found
    """
    service_map_path = get_memories_dir() / "service_map.json"
    if not service_map_path.exists():
        return {
            "enabled": is_service_map_enabled(),
            "last_updated": datetime.now(UTC).isoformat(),
            "assets": [],
            "edges": [],
            "history": [],
        }

    try:
        with service_map_path.open("r") as f:
            return cast(ServiceMap, json.load(f))
    except (json.JSONDecodeError, OSError):
        return {
            "enabled": is_service_map_enabled(),
            "last_updated": datetime.now(UTC).isoformat(),
            "assets": [],
            "edges": [],
            "history": [],
        }


def persist_service_map(service_map: ServiceMap) -> Path:
    """Persist service map to disk (overwrite).

    Args:
        service_map: ServiceMap to persist

    Returns:
        Path to written file
    """
    # Ensure history is capped at 20 entries before persisting
    if len(service_map.get("history", [])) > 20:
        service_map["history"] = service_map["history"][-20:]

    service_map_path = get_memories_dir() / "service_map.json"
    service_map_path.parent.mkdir(parents=True, exist_ok=True)

    with service_map_path.open("w") as f:
        json.dump(service_map, f, indent=2)

    return service_map_path


def get_compact_asset_inventory(service_map: ServiceMap, limit: int = 10) -> str:
    """Get compact asset inventory summary for memory embedding.

    Args:
        service_map: ServiceMap to summarize
        limit: Max assets to include

    Returns:
        Compact asset inventory string
    """
    if not service_map["enabled"] or not service_map["assets"]:
        return "No assets discovered."

    # Sort by investigation_count (hotspots first)
    sorted_assets = sorted(
        service_map["assets"],
        key=lambda a: a.get("investigation_count", 0),
        reverse=True,
    )

    lines = []
    for asset in sorted_assets[:limit]:
        count = asset.get("investigation_count", 1)
        confidence = asset.get("confidence", 1.0)
        # Add marker for tentative assets
        if asset.get("verification_status") == "needs_verification":
            status_marker = "?"
        else:
            status_marker = ""
        lines.append(
            f"- {asset['type']}: {asset['name']} "
            f"(investigated {count}x, confidence={confidence:.1f}){status_marker}"
        )

    if len(sorted_assets) > limit:
        lines.append(f"... +{len(sorted_assets) - limit} more assets")

    return "\n".join(lines)
