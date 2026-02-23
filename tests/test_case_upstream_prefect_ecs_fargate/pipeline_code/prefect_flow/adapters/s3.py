"""
S3 adapter for reading and writing JSON data.

Instrumented with OpenTelemetry using AWS S3 semantic conventions:
- aws.s3.bucket: The S3 bucket name
- aws.s3.key: The S3 object key
- aws.s3.operation: The operation type (GetObject, PutObject)

Note: BotocoreInstrumentor also instruments boto3 calls automatically,
so these spans provide higher-level business context while the auto-
instrumentation captures low-level HTTP details.
"""

import json
from datetime import UTC, datetime

import boto3
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from ..errors import SystemError

tracer = trace.get_tracer(__name__)
s3_client = boto3.client("s3")


def read_json(bucket: str, key: str) -> tuple[dict, str]:
    """
    Read JSON from S3 and return data and correlation_id.

    Creates a span with AWS S3 semantic conventions for observability.
    """
    with tracer.start_as_current_span("s3.get_object") as span:
        # AWS S3 semantic conventions
        span.set_attribute("aws.s3.bucket", bucket)
        span.set_attribute("aws.s3.key", key)
        span.set_attribute("aws.s3.operation", "GetObject")

        try:
            response = s3_client.get_object(Bucket=bucket, Key=key)
            body = response["Body"].read()
            span.set_attribute("aws.s3.content_length", len(body))

            data = json.loads(body.decode())
            correlation_id = response.get("Metadata", {}).get("correlation_id", "unknown")

            span.set_attribute("data.correlation_id", correlation_id)
            span.set_attribute("data.record.count", len(data.get("data", [])))

            return data, correlation_id

        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise SystemError(f"Failed to read from S3: {e}") from e


def write_json(bucket: str, key: str, data: dict, correlation_id: str, source_key: str):
    """
    Write JSON to S3 with metadata.

    Creates a span with AWS S3 semantic conventions for observability.
    """
    with tracer.start_as_current_span("s3.put_object") as span:
        # AWS S3 semantic conventions
        span.set_attribute("aws.s3.bucket", bucket)
        span.set_attribute("aws.s3.key", key)
        span.set_attribute("aws.s3.operation", "PutObject")
        span.set_attribute("data.correlation_id", correlation_id)
        span.set_attribute("data.source_key", source_key)

        try:
            body = json.dumps(data, indent=2)
            span.set_attribute("aws.s3.content_length", len(body))
            span.set_attribute("data.record.count", len(data.get("data", [])))

            s3_client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
                Metadata={
                    "correlation_id": correlation_id,
                    "source_key": source_key,
                    "processed_at": datetime.now(UTC).isoformat(),
                },
            )

        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise SystemError(f"Failed to write to S3: {e}") from e
