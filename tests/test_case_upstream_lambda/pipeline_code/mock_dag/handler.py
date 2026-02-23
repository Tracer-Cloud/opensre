"""
Upstream/Downstream Pipeline - Orchestration Layer.

Follows Senior/Staff-level refactoring principles:
1. Split domain logic from infrastructure (domain.py).
2. Introduce explicit error types (errors.py).
3. Thin, testable adapters for S3 and Alerting (adapters/).
4. Explicit schemas and contracts (schemas.py).
5. File layout optimized for intent.
"""

import json
import time
from contextlib import contextmanager

from .adapters.alerting import fire_pipeline_alert
from .adapters.s3 import read_json, write_json
from .config import PIPELINE_NAME, PROCESSED_BUCKET, REQUIRED_FIELDS
from .domain import transform_data as domain_transform_data
from .domain import validate_data as domain_validate_data
from .errors import PipelineError


class _NoopSpan:
    def set_attribute(self, *_args, **_kwargs) -> None:
        return None


class _NoopTracer:
    @contextmanager
    def start_as_current_span(self, *_args, **_kwargs):
        yield _NoopSpan()

def lambda_handler(event, context):
    """
    Entrypoint: Adapts S3 events to Domain Logic.

    Responsibilities:
    - Extract infrastructure details (bucket, key).
    - Coordinate adapters and domain logic.
    - Centralized error handling and alerting.
    """
    tracer = _NoopTracer()
    correlation_id = "unknown"

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]

        with tracer.start_as_current_span("process_s3_record") as span:
            span.set_attribute("s3.bucket", bucket)
            span.set_attribute("s3.key", key)

            try:
                # 1. Extraction (Infrastructure)
                raw_payload, correlation_id = read_json(bucket, key)
                raw_records = raw_payload.get("data", [])
                span.set_attribute("record_count", len(raw_records))
                span.set_attribute("correlation_id", correlation_id)
                execution_run_id = correlation_id
                span.set_attribute("execution.run_id", execution_run_id)

                # Log structured input for traceability
                print(
                    json.dumps(
                        {
                            "event": "processing_started",
                            "input_bucket": bucket,
                            "input_key": key,
                            "correlation_id": correlation_id,
                            "execution_run_id": execution_run_id,
                            "record_count": len(raw_records),
                        }
                    )
                )

                # 2. Processing (Domain Logic - Pure)
                with tracer.start_as_current_span("validate_data") as validate_span:
                    validate_span.set_attribute("execution.run_id", execution_run_id)
                    validate_span.set_attribute("record_count", len(raw_records))
                    validate_span.set_attribute("correlation_id", correlation_id)
                    domain_validate_data(raw_records, REQUIRED_FIELDS)

                with tracer.start_as_current_span("transform_data") as transform_span:
                    transform_span.set_attribute("execution.run_id", execution_run_id)
                    transform_span.set_attribute("record_count", len(raw_records))
                    transform_span.set_attribute("correlation_id", correlation_id)
                    processed_records = domain_transform_data(raw_records)

                # 3. Loading (Infrastructure)
                output_key = key.replace("ingested/", "processed/")
                output_payload = {"data": [r.to_dict() for r in processed_records]}

                write_json(
                    bucket=PROCESSED_BUCKET,
                    key=output_key,
                    data=output_payload,
                    correlation_id=correlation_id,
                    source_key=key,
                )

            except PipelineError as e:
                # Domain or System errors caught and alerted
                fire_pipeline_alert(PIPELINE_NAME, bucket, key, correlation_id, e)
                raise

            except Exception as e:
                # Unexpected system-level crashes
                fire_pipeline_alert(PIPELINE_NAME, bucket, key, correlation_id, e)
                raise

    return {"status": "success", "correlation_id": correlation_id}
