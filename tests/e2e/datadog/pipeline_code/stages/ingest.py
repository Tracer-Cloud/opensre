"""Ingest stage: read variant records from RECORDS_JSON env var (k8s) or defaults."""

import json
import os
import sys

from config import PIPELINE_NAME, PIPELINE_RUN_ID

_STAGING_PATH = "/tmp/staging"

_DEFAULT_RECORDS = [
    {
        "sample_id": "S001",
        "gene": "BRCA1",
        "chromosome": "17",
        "position": 43044295,
        "ref_allele": "A",
        "alt_allele": "G",
        "quality_score": 99.2,
    },
    {
        "sample_id": "S002",
        "gene": "TP53",
        "chromosome": "17",
        "position": 7674220,
        "ref_allele": "C",
        "alt_allele": "T",
        "quality_score": 87.5,
    },
]


def main() -> None:
    os.makedirs(_STAGING_PATH, exist_ok=True)
    output = os.path.join(_STAGING_PATH, f"{PIPELINE_RUN_ID}_raw.json")

    records_env = os.environ.get("RECORDS_JSON")
    if records_env:
        try:
            records = json.loads(records_env)
        except json.JSONDecodeError as e:
            print(f"PIPELINE_ERROR: Invalid RECORDS_JSON: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        records = _DEFAULT_RECORDS

    with open(output, "w") as f:
        json.dump({"pipeline": PIPELINE_NAME, "run_id": PIPELINE_RUN_ID, "records": records}, f)

    print(
        json.dumps(
            {
                "stage": "ingest",
                "status": "success",
                "pipeline": PIPELINE_NAME,
                "run_id": PIPELINE_RUN_ID,
                "record_count": len(records),
                "output": output,
            }
        )
    )
