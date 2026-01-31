"""
Upstream/Downstream Pipeline - Orchestration Layer.

Follows Senior/Staff-level refactoring principles:
1. Split domain logic from infrastructure (domain.py).
2. Introduce explicit error types (errors.py).
3. Thin, testable adapters for S3 and Alerting (adapters/).
4. Explicit schemas and contracts (schemas.py).
5. File layout optimized for intent.
"""

from .adapters.alerting import fire_pipeline_alert
from .adapters.s3 import read_json, write_json
from .config import PIPELINE_NAME, PROCESSED_BUCKET, REQUIRED_FIELDS
from .domain import validate_and_transform
from .errors import PipelineError


def lambda_handler(event, context):
    """
    Entrypoint: Adapts S3 events to Domain Logic.

    Responsibilities:
    - Extract infrastructure details (bucket, key).
    - Coordinate adapters and domain logic.
    - Centralized error handling and alerting.
    """
    correlation_id = "unknown"

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]

        try:
            # 1. Extraction (Infrastructure)
            raw_payload, correlation_id = read_json(bucket, key)
            raw_records = raw_payload.get("data", [])

            # 2. Processing (Domain Logic - Pure)
            processed_records = validate_and_transform(raw_records, REQUIRED_FIELDS)

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
