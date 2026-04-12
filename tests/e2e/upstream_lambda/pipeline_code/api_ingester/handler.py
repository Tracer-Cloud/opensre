"""API Ingester Lambda - Fetches data from external API and writes to S3.

This Lambda:
1. Calls Mock External API to fetch data
2. Writes raw data to S3 landing bucket
3. S3 event automatically triggers Mock DAG Lambda

No Airflow triggering - using S3 events instead.
"""

import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import boto3
import requests

s3_client = boto3.client("s3")
PIPELINE_NAME = "upstream_downstream_pipeline_lambda_ingester"


class _NoopSpan:
    def set_attribute(self, *_args, **_kwargs) -> None:
        return None


class _NoopTracer:
    @contextmanager
    def start_as_current_span(self, *_args, **_kwargs):
        yield _NoopSpan()


def fetch_from_external_api(
    api_url: str, inject_schema_change: bool = False, logger: logging.Logger = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch data from external API.

    Args:
        api_url: External API base URL
        inject_schema_change: If True, configure API to inject schema change

    Returns:
        Tuple of (API response data, audit info with request/response details)
    """
    audit_info = {"requests": []}

    if inject_schema_change:
        try:
            config_response = requests.post(
                f"{api_url}/config",
                json={"inject_schema_change": True},
                timeout=10,
            )
            logger.info("Configured external API to inject schema change")
            audit_info["requests"].append(
                {
                    "type": "POST",
                    "url": f"{api_url}/config",
                    "request_body": {"inject_schema_change": True},
                    "status_code": config_response.status_code,
                    "response_body": config_response.json() if config_response.ok else None,
                }
            )
        except Exception as e:
            logger.warning(f"Could not configure API: {e}")

    response = requests.get(f"{api_url}/data", timeout=30)
    response.raise_for_status()

    result = response.json()
    schema_version = result.get("meta", {}).get("schema_version", "unknown")
    logger.info(f"Fetched from external API: schema_version={schema_version}")

    # Log structured request/response for audit
    audit_info["requests"].append(
        {
            "type": "GET",
            "url": f"{api_url}/data",
            "status_code": response.status_code,
            "response_body": result,
            "schema_version": schema_version,
        }
    )
    logger.info(f"EXTERNAL_API_AUDIT: {json.dumps(audit_info)}")

    return result, audit_info


def lambda_handler(event: dict, context: Any) -> dict:
    """Lambda handler for API ingestion.

    Supports both direct Lambda invoke and API Gateway triggers.

    Event parameters:
    - inject_schema_change: bool - If true, API returns bad schema
    - correlation_id: str - Optional correlation ID for tracing

    Returns:
        dict with s3_key, bucket, and status
    """
    tracer = _NoopTracer()
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)  # Ensure logger level is set

    # Handle API Gateway event format
    if "body" in event and isinstance(event.get("body"), str):
        try:
            body = json.loads(event["body"])
            inject_schema_change = body.get("inject_schema_change", False)
            correlation_id = (
                body.get("correlation_id") or f"ing-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
            )
        except json.JSONDecodeError:
            inject_schema_change = False
            correlation_id = f"ing-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
    else:
        # Direct Lambda invoke
        inject_schema_change = event.get("inject_schema_change", False)
        correlation_id = (
            event.get("correlation_id") or f"ing-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
        )

    landing_bucket = os.environ.get("LANDING_BUCKET")
    external_api_url = os.environ.get("EXTERNAL_API_URL")

    missing_env = []
    if not landing_bucket:
        missing_env.append("LANDING_BUCKET")
    if not external_api_url:
        missing_env.append("EXTERNAL_API_URL")
    if missing_env:
        error_message = f"Missing required environment variables: {', '.join(missing_env)}"
        logger.error(
            json.dumps(
                {
                    "event": "config_error",
                    "execution_run_id": correlation_id,
                    "correlation_id": correlation_id,
                    "missing_env": missing_env,
                    "error": error_message,
                }
            )
        )
        payload = {
            "error": error_message,
            "missing_env": missing_env,
            "correlation_id": correlation_id,
            "hint": "Ensure the Lambda environment includes LANDING_BUCKET and EXTERNAL_API_URL.",
        }
        if "body" in event:
            return {
                "statusCode": 500,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(payload),
            }
        payload["statusCode"] = 500
        return payload

    execution_run_id = correlation_id
    logger.info(
        json.dumps(
            {
                "event": "lambda_invocation",
                "execution_run_id": execution_run_id,
                "correlation_id": correlation_id,
            }
        )
    )

    # Fetch data from external API
    try:
        with tracer.start_as_current_span("fetch_external_api") as span:
            span.set_attribute("correlation_id", correlation_id)
            span.set_attribute("execution.run_id", execution_run_id)
            span.set_attribute("inject_schema_change", inject_schema_change)
            api_response, audit_info = fetch_from_external_api(
                external_api_url, inject_schema_change, logger
            )
            data = api_response.get("data", [])
            api_meta = api_response.get("meta", {})
            span.set_attribute("record_count", len(data))
        logger.info(
            json.dumps(
                {
                    "event": "external_api_fetch_complete",
                    "execution_run_id": execution_run_id,
                    "record_count": len(data),
                }
            )
        )
    except Exception as e:
        logger.error(
            json.dumps(
                {
                    "event": "external_api_error",
                    "execution_run_id": execution_run_id,
                    "error": str(e),
                }
            )
        )
        return {
            "statusCode": 500,
            "error": f"External API call failed: {str(e)}",
            "correlation_id": correlation_id,
        }

    # Write to S3
    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
    s3_key = f"ingested/{timestamp}/data.json"
    audit_key = f"audit/{correlation_id}.json"

    try:
        with tracer.start_as_current_span("write_s3_objects") as span:
            span.set_attribute("s3.bucket", landing_bucket)
            span.set_attribute("correlation_id", correlation_id)
            span.set_attribute("execution.run_id", execution_run_id)
            span.set_attribute("record_count", len(data))

            # Write audit object with external API request/response details
            audit_payload = {
                "correlation_id": correlation_id,
                "timestamp": timestamp,
                "external_api_url": external_api_url,
                "audit_info": audit_info,
            }
            s3_client.put_object(
                Bucket=landing_bucket,
                Key=audit_key,
                Body=json.dumps(audit_payload, indent=2),
                ContentType="application/json",
            )
            logger.info(
                json.dumps(
                    {
                        "event": "s3_write_complete",
                        "execution_run_id": execution_run_id,
                        "audit_key": audit_key,
                    }
                )
            )

            # Write main data with audit_key in metadata
            s3_client.put_object(
                Bucket=landing_bucket,
                Key=s3_key,
                Body=json.dumps(api_response, indent=2),
                ContentType="application/json",
                Metadata={
                    "correlation_id": correlation_id,
                    "source": "api_ingester_lambda",
                    "timestamp": timestamp,
                    "schema_version": api_meta.get("schema_version", "unknown"),
                    "schema_change_injected": str(inject_schema_change),
                    "audit_key": audit_key,
                },
            )
            logger.info(
                json.dumps(
                    {
                        "event": "s3_data_written",
                        "execution_run_id": execution_run_id,
                        "s3_key": s3_key,
                        "record_count": len(data),
                        "correlation_id": correlation_id,
                        "schema_version": api_meta.get("schema_version"),
                    }
                )
            )
    except Exception as e:
        logger.error(
            json.dumps(
                {
                    "event": "s3_write_error",
                    "execution_run_id": execution_run_id,
                    "error": str(e),
                }
            )
        )
        return {
            "statusCode": 500,
            "error": f"S3 write failed: {str(e)}",
            "correlation_id": correlation_id,
        }

    result = {
        "statusCode": 200,
        "s3_key": s3_key,
        "s3_bucket": landing_bucket,
        "record_count": len(data),
        "correlation_id": correlation_id,
        "execution_run_id": execution_run_id,
        "schema_version": api_meta.get("schema_version"),
        "schema_change_injected": inject_schema_change,
    }

    # Format for API Gateway if called via HTTP
    if "body" in event:
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(result),
        }

    return result
