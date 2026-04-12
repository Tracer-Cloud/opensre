"""Centralized config manager for Kubernetes trigger API URL."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.e2e.kubernetes.infrastructure_sdk.eks import (
    ECR_REPO_NAME,
    REGION,
    STACK_NAME,
    TRIGGER_LAMBDA_NAME,
)
from tests.shared.infrastructure_sdk.config import OUTPUTS_DIR, load_outputs
from tests.shared.infrastructure_sdk.deployer import get_boto3_client

TRIGGER_CONFIG_NAME = "tracer-k8s-trigger"
TRIGGER_CONFIG_PATH = OUTPUTS_DIR / f"{TRIGGER_CONFIG_NAME}.json"


def load_trigger_config() -> dict[str, Any]:
    if not TRIGGER_CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Missing trigger config: {TRIGGER_CONFIG_PATH}. Run: make regen-trigger-config"
        )

    with open(TRIGGER_CONFIG_PATH) as f:
        payload = json.load(f)

    trigger_api_url = (payload.get("trigger_api_url") or "").strip()
    if not trigger_api_url:
        raise ValueError(
            f"Invalid trigger config (missing trigger_api_url): {TRIGGER_CONFIG_PATH}. "
            "Run: make regen-trigger-config"
        )
    return payload


def save_trigger_config(trigger_api_url: str) -> Path:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "stack_name": STACK_NAME,
        "region": REGION,
        "trigger_api_url": trigger_api_url.rstrip("/"),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    with open(TRIGGER_CONFIG_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    return TRIGGER_CONFIG_PATH


def _discover_trigger_api_url() -> str | None:
    # First: check SDK outputs file if it has trigger API URL
    try:
        outputs = load_outputs(STACK_NAME)
        from_outputs = (outputs.get("trigger_api_url") or "").strip().rstrip("/")
        if from_outputs:
            return from_outputs
    except FileNotFoundError:
        # Outputs file not written yet; fall through to AWS tag-based discovery
        pass

    # Fallback: discover API Gateway via AWS tags
    tagger = get_boto3_client("resourcegroupstaggingapi", REGION)
    rest_api_id = None
    paginator = tagger.get_paginator("get_resources")
    for page in paginator.paginate(
        TagFilters=[
            {"Key": "tracer:stack", "Values": [STACK_NAME]},
            {"Key": "tracer:managed", "Values": ["sdk"]},
        ],
        ResourceTypeFilters=["apigateway:restapis"],
    ):
        for resource in page.get("ResourceTagMappingList", []):
            arn = resource.get("ResourceARN", "")
            marker = "/restapis/"
            if marker in arn:
                rest_api_id = arn.split(marker, 1)[1].split("/", 1)[0]
                break
        if rest_api_id:
            break

    if not rest_api_id:
        return None

    api_client = get_boto3_client("apigateway", REGION)
    stages = api_client.get_stages(restApiId=rest_api_id).get("item", [])
    stage_name = "prod"
    if not any(stage.get("stageName") == "prod" for stage in stages):
        stage_name = stages[0]["stageName"] if stages else "prod"

    return f"https://{rest_api_id}.execute-api.{REGION}.amazonaws.com/{stage_name}"


def discover_runtime_outputs() -> dict[str, str] | None:
    """Discover runtime outputs without mutating infrastructure."""
    try:
        outputs = load_outputs(STACK_NAME)
        if (
            outputs.get("landing_bucket")
            and outputs.get("processed_bucket")
            and outputs.get("ecr_image_uri")
        ):
            return {
                "landing_bucket": outputs["landing_bucket"],
                "processed_bucket": outputs["processed_bucket"],
                "ecr_image_uri": outputs["ecr_image_uri"],
            }
    except FileNotFoundError:
        # Outputs file not written yet; fall through to Lambda env-var discovery
        pass

    lambda_client = get_boto3_client("lambda", REGION)
    try:
        env = (
            lambda_client.get_function_configuration(FunctionName=TRIGGER_LAMBDA_NAME)
            .get("Environment", {})
            .get("Variables", {})
        )
    except Exception:
        env = {}

    landing = (env.get("LANDING_BUCKET") or "").strip()
    processed = (env.get("PROCESSED_BUCKET") or "").strip()
    image_uri = (env.get("IMAGE_URI") or "").strip()
    if landing and processed and image_uri:
        return {
            "landing_bucket": landing,
            "processed_bucket": processed,
            "ecr_image_uri": image_uri,
        }

    tagger = get_boto3_client("resourcegroupstaggingapi", REGION)
    s3_client = get_boto3_client("s3", REGION)
    ecr_client = get_boto3_client("ecr", REGION)
    created_at = {
        bucket["Name"]: bucket["CreationDate"]
        for bucket in s3_client.list_buckets().get("Buckets", [])
    }

    bucket_names: list[str] = []
    paginator = tagger.get_paginator("get_resources")
    for page in paginator.paginate(
        TagFilters=[
            {"Key": "tracer:stack", "Values": [STACK_NAME]},
            {"Key": "tracer:managed", "Values": ["sdk"]},
        ],
        ResourceTypeFilters=["s3"],
    ):
        for resource in page.get("ResourceTagMappingList", []):
            arn = resource.get("ResourceARN", "")
            if arn.startswith("arn:aws:s3:::"):
                bucket_names.append(arn.split(":::")[1])

    landing_buckets = [b for b in bucket_names if b.startswith("tracer-k8s-landing-")]
    processed_buckets = [b for b in bucket_names if b.startswith("tracer-k8s-processed-")]
    if not landing_buckets or not processed_buckets:
        return None

    def _latest_bucket(names: list[str]) -> str:
        return max(names, key=lambda n: created_at.get(n))

    try:
        repo = ecr_client.describe_repositories(repositoryNames=[ECR_REPO_NAME])
        repo_uri = repo["repositories"][0]["repositoryUri"]
        image_uri = f"{repo_uri}:latest"
    except Exception:
        return None

    return {
        "landing_bucket": _latest_bucket(landing_buckets),
        "processed_bucket": _latest_bucket(processed_buckets),
        "ecr_image_uri": image_uri,
    }


def regenerate_trigger_config() -> Path:
    trigger_api_url = _discover_trigger_api_url()
    if not trigger_api_url:
        raise RuntimeError(
            "Could not discover trigger API URL from AWS tags. "
            "Ensure trigger API exists and is tagged with tracer:stack=tracer-eks-k8s-test."
        )
    return save_trigger_config(trigger_api_url)
