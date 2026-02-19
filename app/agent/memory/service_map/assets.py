"""Asset extraction and inference for the service map."""

import json
import re
from datetime import UTC, datetime
from typing import Any

from app.agent.nodes.publish_findings.formatters.infrastructure import (
    extract_infrastructure_assets,
)

from .identifiers import generate_asset_id
from .types import Asset, Edge


def _build_asset(
    asset_type: str,
    name: str,
    *,
    now: str,
    pipeline_name: str,
    alert_name: str,
    asset_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    aws_arn: str | None = None,
    confidence: float = 1.0,
    verification_status: str = "verified",
    pipeline_context: list[str] | None = None,
    alert_context: list[str] | None = None,
) -> Asset:
    if metadata is None:
        metadata = {}
    if pipeline_context is None:
        pipeline_context = [pipeline_name] if pipeline_name else []
    if alert_context is None:
        alert_context = [alert_name] if alert_name else []

    return {
        "id": asset_id or generate_asset_id(asset_type, name),
        "type": asset_type,
        "name": name,
        "aws_arn": aws_arn,
        "pipeline_context": pipeline_context,
        "alert_context": alert_context,
        "investigation_count": 1,
        "last_investigated": now,
        "confidence": confidence,
        "verification_status": verification_status,
        "metadata": metadata,
    }


def extract_assets_from_infrastructure(
    ctx: dict[str, Any], pipeline_name: str, alert_name: str
) -> list[Asset]:
    """Extract assets from infrastructure extraction."""
    from app.agent.nodes.publish_findings.context.models import ReportContext

    report_ctx: ReportContext = {
        "pipeline_name": pipeline_name,
        "root_cause": "",
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
            _build_asset(
                "api_gateway",
                infra_assets["api_gateway"],
                now=now,
                pipeline_name=pipeline_name,
                alert_name=alert_name,
            )
        )

    # Lambda functions
    lambda_assets_seen: set[str] = set()
    for lambda_func in infra_assets.get("lambda_functions", []):
        func_name = lambda_func["name"]
        role = lambda_func.get("role", "")

        # Override role from annotations if this is marked as trigger lambda
        raw_alert = ctx.get("raw_alert", {})
        annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {})
        trigger_lambda = annotations.get("trigger_lambda") or annotations.get("ingestion_lambda")
        if trigger_lambda and func_name == trigger_lambda:
            role = "trigger"

        lambda_id = generate_asset_id("lambda", func_name)
        lambda_assets_seen.add(lambda_id)
        assets.append(
            _build_asset(
                "lambda",
                func_name,
                now=now,
                pipeline_name=pipeline_name,
                alert_name=alert_name,
                metadata={"role": role, "runtime": lambda_func.get("runtime")},
            )
        )

    # Fallback: extract Lambda from evidence if not found via infrastructure
    evidence = ctx.get("evidence", {})
    lambda_function = evidence.get("lambda_function", {})
    if lambda_function and lambda_function.get("function_name"):
        func_name = lambda_function["function_name"]
        lambda_id = generate_asset_id("lambda", func_name)
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
                _build_asset(
                    "lambda",
                    func_name,
                    now=now,
                    pipeline_name=pipeline_name,
                    alert_name=alert_name,
                    metadata={"role": role, "runtime": lambda_function.get("runtime")},
                )
            )

    # S3 buckets - deduplicate by bucket name
    s3_buckets_seen: dict[str, Asset] = {}
    for s3_bucket in infra_assets.get("s3_buckets", []):
        bucket_name = s3_bucket["name"]
        bucket_id = generate_asset_id("s3_bucket", bucket_name)

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
            asset = _build_asset(
                "s3_bucket",
                bucket_name,
                now=now,
                pipeline_name=pipeline_name,
                alert_name=alert_name,
                metadata={
                    "bucket_type": s3_bucket.get("type"),
                    "keys": [s3_bucket.get("key")] if s3_bucket.get("key") else [],
                },
            )
            s3_buckets_seen[bucket_id] = asset
            assets.append(asset)

    # ECS service
    if infra_assets.get("ecs_service"):
        ecs = infra_assets["ecs_service"]
        cluster = ecs.get("cluster")
        flow_name = ecs.get("flow_name")
        if cluster:
            assets.append(
                _build_asset(
                    "ecs_cluster",
                    cluster,
                    now=now,
                    pipeline_name=pipeline_name,
                    alert_name=alert_name,
                    metadata={"flow_name": flow_name, "task_arn": ecs.get("task")},
                )
            )

    # Batch service
    if infra_assets.get("batch_service"):
        batch = infra_assets["batch_service"]
        queue = batch.get("queue")
        if queue:
            assets.append(
                _build_asset(
                    "batch_queue",
                    queue,
                    now=now,
                    pipeline_name=pipeline_name,
                    alert_name=alert_name,
                    metadata={"job_definition": batch.get("definition")},
                )
            )

    # CloudWatch log groups
    for log_group in infra_assets.get("log_groups", []):
        lg_name = log_group["name"]
        assets.append(
            _build_asset(
                "cloudwatch_log_group",
                lg_name,
                now=now,
                pipeline_name=pipeline_name,
                alert_name=alert_name,
                metadata={"log_type": log_group.get("type")},
            )
        )

    # Pipeline
    if infra_assets.get("pipeline"):
        assets.append(
            _build_asset(
                "pipeline",
                infra_assets["pipeline"],
                now=now,
                pipeline_name=pipeline_name,
                alert_name=alert_name,
            )
        )

    return assets


def _extract_external_api_url(evidence: dict[str, Any]) -> str:
    audit_payload = evidence.get("s3_audit_payload", {})
    audit_content = audit_payload.get("content", {})
    if isinstance(audit_content, str):
        try:
            audit_content = json.loads(audit_content)
        except json.JSONDecodeError:
            audit_content = {}

    if not isinstance(audit_content, dict):
        return "unknown"

    url: str = audit_content.get("external_api_url", "unknown")
    return url


def ensure_edge_endpoint_assets(
    edges: list[Edge], evidence: dict[str, Any], pipeline_name: str, alert_name: str
) -> list[Asset]:
    """Ensure all edge endpoints exist as assets (create if missing)."""
    assets: list[Asset] = []
    now = datetime.now(UTC).isoformat()
    seen_ids: set[str] = set()

    for edge in edges:
        for asset_id in (edge["from_asset"], edge["to_asset"]):
            if asset_id in seen_ids:
                continue
            seen_ids.add(asset_id)

            # Parse asset type and name from ID
            asset_type, asset_name = asset_id.split(":", 1)

            # Special handling for external_api: extract URL from audit payload
            if asset_type == "external_api":
                api_url = _extract_external_api_url(evidence)
                assets.append(
                    _build_asset(
                        asset_type,
                        api_url,
                        now=now,
                        pipeline_name=pipeline_name,
                        alert_name=alert_name,
                        asset_id=asset_id,
                        pipeline_context=[],
                        alert_context=[],
                        confidence=0.8,
                        metadata={"inferred_from": "audit_payload"},
                    )
                )
            else:
                # Create minimal asset for other types
                assets.append(
                    _build_asset(
                        asset_type,
                        asset_name,
                        now=now,
                        pipeline_name=pipeline_name,
                        alert_name=alert_name,
                        confidence=0.9,
                        metadata={"created_from": "edge_endpoint"},
                    )
                )

    return assets


def infer_tentative_assets_from_alert(
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
                    _build_asset(
                        "s3_bucket",
                        "tentative_destination",
                        now=now,
                        pipeline_name="",
                        alert_name=alert_name,
                        pipeline_context=[],
                        alert_context=[alert_name],
                        confidence=0.6,
                        verification_status="needs_verification",
                        metadata={"inferred_from": "alert_text"},
                    )
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
                    _build_asset(
                        "s3_bucket",
                        "tentative_missing",
                        now=now,
                        pipeline_name="",
                        alert_name=alert_name,
                        pipeline_context=[],
                        alert_context=[alert_name],
                        confidence=0.6,
                        verification_status="needs_verification",
                        metadata={"inferred_from": "alert_text"},
                    )
                )

    return tentative_assets, tentative_edges
