"""
Simulated Customer Pipeline - Pure Business Logic.

This is what CUSTOMER CODE looks like - just business logic.
No CloudWatch, no logging infrastructure, no observability code.
"""

import os
import sys
import uuid
from pathlib import Path

# Add shared telemetry to path
_test_root = Path(__file__).parent.parent
sys.path.insert(0, str(_test_root / "shared" / "telemetry"))

from tracer_telemetry import init_telemetry

_pipeline_context = {
    "pipeline_name": "demo_pipeline_cloudwatch",
    "initialized": False,
}

# Initialize telemetry
_telemetry = None
_tracer = None


def extract_and_validate(input_path: str, execution_run_id: str) -> str:
    with _tracer.start_as_current_span("extract_data") as span:
        span.set_attribute("execution.run_id", execution_run_id)
        span.set_attribute("input_path", input_path)
        
        if not os.path.exists(input_path):
            span.set_attribute("error", True)
            span.set_attribute("error.message", f"empty file not present: {input_path}")
            raise FileNotFoundError(f"empty file not present: {input_path}")

        with open(input_path) as f:
            data = f.read()

        if not data or len(data) == 0:
            span.set_attribute("error", True)
            span.set_attribute("error.message", "empty dataset")
            raise ValueError("empty dataset")

        span.set_attribute("data_size", len(data))
        return data


def transform_data(data: str, execution_run_id: str) -> list[dict]:
    with _tracer.start_as_current_span("transform_data") as span:
        span.set_attribute("execution.run_id", execution_run_id)
        rows = data.split("\n")
        transformed = [{"line": i, "content": row} for i, row in enumerate(rows)]
        span.set_attribute("record_count", len(transformed))
        return transformed


def write_output(transformed_data: list[dict], output_path: str, execution_run_id: str) -> int:
    with _tracer.start_as_current_span("load_data") as span:
        span.set_attribute("execution.run_id", execution_run_id)
        span.set_attribute("output_path", output_path)
        span.set_attribute("record_count", len(transformed_data))
        return len(transformed_data)


def main() -> dict:
    global _telemetry, _tracer
    
    # Initialize telemetry
    _telemetry = init_telemetry(
        service_name="cloudwatch-demo",
        resource_attributes={
            "pipeline.name": "demo_pipeline_cloudwatch",
            "pipeline.type": "batch",
        },
    )
    _tracer = _telemetry.tracer
    
    _pipeline_context["initialized"] = True
    execution_run_id = str(uuid.uuid4())

    input_file = "/data/input.csv"
    output_file = "/data/output.parquet"

    with _tracer.start_as_current_span("process_pipeline") as root_span:
        root_span.set_attribute("execution.run_id", execution_run_id)
        root_span.set_attribute("pipeline.name", _pipeline_context["pipeline_name"])
        
        raw_data = extract_and_validate(input_file, execution_run_id)
        transformed = transform_data(raw_data, execution_run_id)
        rows = write_output(transformed, output_file, execution_run_id)
        
        root_span.set_attribute("rows_processed", rows)
        root_span.set_attribute("status", "success")

    # Flush telemetry for short-lived process
    _telemetry.flush()

    return {
        "pipeline_name": _pipeline_context["pipeline_name"],
        "status": "success",
        "rows_processed": rows,
        "execution_run_id": execution_run_id,
    }


if __name__ == "__main__":
    sys.exit(main())
