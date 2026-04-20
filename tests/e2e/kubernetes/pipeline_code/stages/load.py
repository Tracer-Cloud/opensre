"""Load stage: read transformed data from staging, write to processed bucket."""

import json

from adapters.s3 import read_json, write_json
from config import LANDING_BUCKET, PIPELINE_RUN_ID, PROCESSED_BUCKET, staging_key


def main() -> None:
    input_key = staging_key("transformed.json")
    print(f"[load] Reading from s3://{LANDING_BUCKET}/{input_key}")

    payload, correlation_id = read_json(LANDING_BUCKET, input_key)
    records = payload.get("data", [])

    output_key = f"processed/{PIPELINE_RUN_ID}/output.json"
    print(f"[load] Writing {len(records)} records to s3://{PROCESSED_BUCKET}/{output_key}")

    write_json(
        bucket=PROCESSED_BUCKET,
        key=output_key,
        data=payload,
        correlation_id=correlation_id,
        source_key=input_key,
    )

    print(
        json.dumps(
            {
                "stage": "load",
                "status": "success",
                "record_count": len(records),
                "correlation_id": correlation_id,
                "output": f"s3://{PROCESSED_BUCKET}/{output_key}",
            }
        )
    )
