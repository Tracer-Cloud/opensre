"""Edge extraction and inference for the service map."""

import json
from datetime import UTC, datetime
from typing import Any

from .identifiers import generate_asset_id
from .types import Asset, Edge

GRAFANA_DATASOURCE_ASSET_ID = "grafana_datasource:tracerbio"
EXTERNAL_API_ASSET_ID = "external_api:vendor"


def _is_grafana_otlp_endpoint(value: str | None) -> bool:
    """Check if an OTLP endpoint points to Grafana Cloud."""
    return bool(value and ("grafana.net" in value or "grafana.com" in value))


def _build_edge(
    from_asset: str,
    to_asset: str,
    edge_type: str,
    *,
    now: str,
    confidence: float,
    verification_status: str,
    evidence: str,
) -> Edge:
    return {
        "from_asset": from_asset,
        "to_asset": to_asset,
        "type": edge_type,
        "confidence": confidence,
        "verification_status": verification_status,
        "evidence": evidence,
        "first_seen": now,
        "last_seen": now,
    }


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
            _build_edge(
                generate_asset_id("lambda", source),
                generate_asset_id("s3_bucket", bucket),
                "writes_to",
                now=now,
                confidence=1.0,
                verification_status="verified",
                evidence="s3_metadata.source",
            )
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
            _build_edge(
                EXTERNAL_API_ASSET_ID,
                generate_asset_id("lambda", lambda_name),
                "triggers",
                now=now,
                confidence=0.9,
                verification_status="verified",
                evidence="audit_payload.external_api_url",
            )
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
                _build_edge(
                    generate_asset_id("lambda", function_name),
                    generate_asset_id("s3_bucket", bucket_name),
                    "writes_to",
                    now=now,
                    confidence=0.9,
                    verification_status="verified",
                    evidence=f"lambda_config.env.{key}",
                )
            )

    return edges


def _extract_grafana_edges(evidence: dict[str, Any], pipeline_name: str) -> list[Edge]:
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

        if otlp_endpoint and _is_grafana_otlp_endpoint(otlp_endpoint):
            function_name = lambda_data["function_name"]

            edges.append(
                _build_edge(
                    generate_asset_id("lambda", function_name),
                    GRAFANA_DATASOURCE_ASSET_ID,
                    "exports_telemetry_to",
                    now=now,
                    confidence=1.0,
                    verification_status="verified",
                    evidence=f"OTEL_EXPORTER_OTLP_ENDPOINT={otlp_endpoint}",
                )
            )

            if pipeline_name:
                edges.append(
                    _build_edge(
                        generate_asset_id("pipeline", pipeline_name),
                        GRAFANA_DATASOURCE_ASSET_ID,
                        "exports_telemetry_to",
                        now=now,
                        confidence=0.9,
                        verification_status="verified",
                        evidence=f"lambda.{function_name}.OTLP→Grafana",
                    )
                )

    # Check ECS task definition for OTLP endpoint
    ecs_task = evidence.get("ecs_task_definition", {})
    if ecs_task.get("taskDefinitionArn"):
        for container in ecs_task.get("containerDefinitions", []):
            env_vars = {e["name"]: e["value"] for e in container.get("environment", [])}

            otlp_endpoint = env_vars.get("OTEL_EXPORTER_OTLP_ENDPOINT") or env_vars.get(
                "GCLOUD_OTLP_ENDPOINT"
            )

            if otlp_endpoint and _is_grafana_otlp_endpoint(otlp_endpoint):
                ecs_cluster = evidence.get("ecs_cluster", {}).get("clusterName", "")
                container_name = container.get("name", "")

                if ecs_cluster:
                    edges.append(
                        _build_edge(
                            generate_asset_id("ecs_cluster", ecs_cluster),
                            GRAFANA_DATASOURCE_ASSET_ID,
                            "exports_telemetry_to",
                            now=now,
                            confidence=1.0,
                            verification_status="verified",
                            evidence=f"ECS.{container_name}.OTLP→Grafana",
                        )
                    )

                if pipeline_name:
                    edges.append(
                        _build_edge(
                            generate_asset_id("pipeline", pipeline_name),
                            GRAFANA_DATASOURCE_ASSET_ID,
                            "exports_telemetry_to",
                            now=now,
                            confidence=0.9,
                            verification_status="verified",
                            evidence=f"ECS.{ecs_cluster}.OTLP→Grafana",
                        )
                    )
                break

    return edges


def extract_edges_from_evidence(
    evidence: dict[str, Any], raw_alert: dict[str, Any], pipeline_name: str
) -> list[Edge]:
    """Extract high-confidence edges directly from evidence fields."""
    edges: list[Edge] = []
    edges.extend(_extract_s3_metadata_edges(evidence))
    edges.extend(_extract_audit_payload_edges(evidence, raw_alert))
    edges.extend(_extract_lambda_config_edges(evidence))
    edges.extend(_extract_grafana_edges(evidence, pipeline_name))
    return edges


def infer_topology_edges(assets: list[Asset]) -> list[Edge]:
    """Infer directed edges from asset topology."""
    edges: list[Edge] = []
    now = datetime.now(UTC).isoformat()

    # Infer Pipeline → ECS/Batch edges
    pipeline_assets = [a for a in assets if a["type"] == "pipeline"]
    ecs_assets = [a for a in assets if a["type"] == "ecs_cluster"]
    batch_assets = [a for a in assets if a["type"] == "batch_queue"]

    for pipeline_asset in pipeline_assets:
        for ecs_asset in ecs_assets:
            edges.append(
                _build_edge(
                    pipeline_asset["id"],
                    ecs_asset["id"],
                    "runs_on",
                    now=now,
                    confidence=1.0,
                    verification_status="verified",
                    evidence="alert_annotations.ecs_cluster",
                )
            )
        for batch_asset in batch_assets:
            edges.append(
                _build_edge(
                    pipeline_asset["id"],
                    batch_asset["id"],
                    "runs_on",
                    now=now,
                    confidence=1.0,
                    verification_status="verified",
                    evidence="alert_annotations.batch_queue",
                )
            )

    # Infer CloudWatch log group associations
    lambda_assets = [a for a in assets if a["type"] == "lambda"]
    log_groups = [a for a in assets if a["type"] == "cloudwatch_log_group"]
    for log_group in log_groups:
        # Associate with Lambda if log group name contains lambda function name
        for lambda_asset in lambda_assets:
            if lambda_asset["name"] in log_group["name"]:
                edges.append(
                    _build_edge(
                        lambda_asset["id"],
                        log_group["id"],
                        "logs_to",
                        now=now,
                        confidence=1.0,
                        verification_status="verified",
                        evidence="log_group_name_pattern",
                    )
                )

        # Associate with ECS/Pipeline if log group name contains cluster/flow
        for ecs_asset in ecs_assets:
            flow_name = ecs_asset.get("metadata", {}).get("flow_name", "")
            if flow_name and flow_name.lower() in log_group["name"].lower():
                edges.append(
                    _build_edge(
                        ecs_asset["id"],
                        log_group["id"],
                        "logs_to",
                        now=now,
                        confidence=0.9,
                        verification_status="verified",
                        evidence="log_group_name_pattern",
                    )
                )

    return edges


def dedupe_edges(edges: list[Edge]) -> list[Edge]:
    edges_by_key: dict[tuple[str, str, str], Edge] = {}
    for edge in edges:
        key = (edge["from_asset"], edge["to_asset"], edge["type"])
        if key not in edges_by_key:
            edges_by_key[key] = edge
            continue

        if edge.get("confidence", 0.0) > edges_by_key[key].get("confidence", 0.0):
            edges_by_key[key] = edge

    return list(edges_by_key.values())
