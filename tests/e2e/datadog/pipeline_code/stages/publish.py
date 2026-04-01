"""Publish stage: write validated records to output. Only runs if validate succeeded."""

import json

from config import PIPELINE_NAME, PIPELINE_RUN_ID

_STAGING_PATH = "/tmp/staging"


def main() -> None:
    input_path = f"{_STAGING_PATH}/{PIPELINE_RUN_ID}_raw.json"
    with open(input_path) as f:
        payload = json.load(f)

    output_path = f"{_STAGING_PATH}/{PIPELINE_RUN_ID}_published.json"
    with open(output_path, "w") as f:
        json.dump(payload, f)

    print(json.dumps({
        "stage": "publish",
        "status": "success",
        "pipeline": PIPELINE_NAME,
        "run_id": PIPELINE_RUN_ID,
        "record_count": len(payload["records"]),
        "output": output_path,
    }))
