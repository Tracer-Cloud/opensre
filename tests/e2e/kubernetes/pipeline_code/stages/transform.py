"""Transform stage: read extracted data, validate schema, transform records."""

import json

from adapters.s3 import read_json, write_json
from config import LANDING_BUCKET, REQUIRED_FIELDS, staging_key
from domain import validate_and_transform


def main() -> None:
    input_key = staging_key("extracted.json")
    print(f"[transform] Reading from s3://{LANDING_BUCKET}/{input_key}")

    raw_payload, correlation_id = read_json(LANDING_BUCKET, input_key)
    raw_records = raw_payload.get("data", [])

    print(f"[transform] Validating {len(raw_records)} records against {REQUIRED_FIELDS}")

    processed = validate_and_transform(raw_records, REQUIRED_FIELDS)

    output_key = staging_key("transformed.json")
    output_payload = {"data": [r.to_dict() for r in processed]}
    write_json(
        bucket=LANDING_BUCKET,
        key=output_key,
        data=output_payload,
        correlation_id=correlation_id,
        source_key=input_key,
    )

    print(json.dumps({
        "stage": "transform",
        "status": "success",
        "input_count": len(raw_records),
        "output_count": len(processed),
        "correlation_id": correlation_id,
        "output": f"s3://{LANDING_BUCKET}/{output_key}",
    }))
