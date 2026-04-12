"""Extract stage: read raw data from S3 landing bucket, write to staging."""

import json

from adapters.s3 import read_json, write_json
from config import LANDING_BUCKET, S3_KEY, staging_key


def main() -> None:
    print(f"[extract] Reading from s3://{LANDING_BUCKET}/{S3_KEY}")

    raw_payload, correlation_id = read_json(LANDING_BUCKET, S3_KEY)
    records = raw_payload.get("data", [])

    print(f"[extract] Extracted {len(records)} records, correlation_id={correlation_id}")

    output_key = staging_key("extracted.json")
    write_json(
        bucket=LANDING_BUCKET,
        key=output_key,
        data=raw_payload,
        correlation_id=correlation_id,
        source_key=S3_KEY,
    )

    print(
        json.dumps(
            {
                "stage": "extract",
                "status": "success",
                "record_count": len(records),
                "correlation_id": correlation_id,
                "output": f"s3://{LANDING_BUCKET}/{output_key}",
            }
        )
    )
