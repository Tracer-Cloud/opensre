"""Validate stage: enforce schema on ingested variant records. Fails on bad data."""

import json
import os
import sys

from config import PIPELINE_NAME, PIPELINE_RUN_ID, REQUIRED_FIELDS
from errors import ValidationError

_STAGING_PATH = "/tmp/staging"


def _load_records() -> list[dict]:
    path = f"{_STAGING_PATH}/{PIPELINE_RUN_ID}_raw.json"
    if not os.path.exists(path):
        records_env = os.environ.get("RECORDS_JSON")
        if not records_env:
            raise FileNotFoundError(f"No staging file at {path} and no RECORDS_JSON env var")
        os.makedirs(_STAGING_PATH, exist_ok=True)
        records = json.loads(records_env)
        with open(path, "w") as f:
            json.dump({"pipeline": PIPELINE_NAME, "run_id": PIPELINE_RUN_ID, "records": records}, f)
        return records
    with open(path) as f:
        return json.load(f)["records"]


def _validate(records: list[dict]) -> None:
    for i, record in enumerate(records):
        missing = [f for f in REQUIRED_FIELDS if f not in record]
        if missing:
            raise ValidationError(
                f"PIPELINE_ERROR: Schema validation failed for record {i} "
                f"(sample_id={record.get('sample_id', '?')}): missing fields {missing}"
            )


def main() -> None:
    records = _load_records()
    print(f"[validate] Checking {len(records)} records against schema {REQUIRED_FIELDS}")

    try:
        _validate(records)
    except ValidationError as e:
        print(str(e), file=sys.stderr)
        print(
            json.dumps(
                {
                    "stage": "validate",
                    "status": "failed",
                    "pipeline": PIPELINE_NAME,
                    "run_id": PIPELINE_RUN_ID,
                    "error": str(e),
                }
            )
        )
        sys.exit(1)

    print(
        json.dumps(
            {
                "stage": "validate",
                "status": "success",
                "pipeline": PIPELINE_NAME,
                "run_id": PIPELINE_RUN_ID,
                "record_count": len(records),
            }
        )
    )
