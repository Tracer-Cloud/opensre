"""
Simulated Customer Pipeline - Pure Business Logic.

This is what CUSTOMER CODE looks like - just business logic.
No CloudWatch, no logging infrastructure, no observability code.
"""

import os
import sys

_pipeline_context = {
    "pipeline_name": "demo_pipeline_cloudwatch",
    "initialized": False,
}


def extract_and_validate(input_path: str) -> str:
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"empty file not present: {input_path}")

    with open(input_path) as f:
        data = f.read()

    if not data or len(data) == 0:
        raise ValueError("empty dataset")

    return data


def transform_data(data: str) -> list[dict]:
    rows = data.split("\n")
    transformed = [{"line": i, "content": row} for i, row in enumerate(rows)]
    return transformed


def write_output(transformed_data: list[dict], output_path: str) -> int:
    return len(transformed_data)


def main() -> dict:
    _pipeline_context["initialized"] = True

    input_file = "/data/input.csv"
    output_file = "/data/output.parquet"

    raw_data = extract_and_validate(input_file)
    transformed = transform_data(raw_data)
    rows = write_output(transformed, output_file)

    return {
        "pipeline_name": _pipeline_context["pipeline_name"],
        "status": "success",
        "rows_processed": rows,
    }


if __name__ == "__main__":
    sys.exit(main())
