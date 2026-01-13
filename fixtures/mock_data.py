"""
Mock data for the demo scenario.

Scenario:
- Service A writes raw file to S3
- Nextflow picks it up and runs transformation
- Nextflow writes processed file but finalize step FAILS
- _SUCCESS marker is NOT written
- Service B is waiting for _SUCCESS
- Warehouse table is stale
"""

from datetime import datetime, timezone

# S3 Mock Data
S3_RAW_BUCKET = "tracer-raw-data"
S3_PROCESSED_BUCKET = "tracer-processed-data"
S3_DATE = "2026-01-13"

S3_RAW_FILES = [
    {
        "key": f"events/{S3_DATE}/events_raw.parquet",
        "size": 15_234_567,
        "last_modified": datetime(2026, 1, 12, 23, 45, 0, tzinfo=timezone.utc),
        "etag": "abc123",
    }
]

S3_PROCESSED_FILES = [
    {
        "key": f"events/{S3_DATE}/events_processed.parquet",
        "size": 12_345_678,
        "last_modified": datetime(2026, 1, 13, 0, 5, 0, tzinfo=timezone.utc),
        "etag": "def456",
    }
    # Note: _SUCCESS marker is MISSING - this is the bug!
]

# Nextflow Mock Data
NEXTFLOW_PIPELINE_ID = "events-etl"
NEXTFLOW_RUN_ID = "run-20260113-001"

NEXTFLOW_RUNS = {
    NEXTFLOW_RUN_ID: {
        "run_id": NEXTFLOW_RUN_ID,
        "pipeline_id": NEXTFLOW_PIPELINE_ID,
        "status": "FAILED",
        "started_at": datetime(2026, 1, 12, 23, 50, 0, tzinfo=timezone.utc),
        "ended_at": datetime(2026, 1, 13, 0, 10, 0, tzinfo=timezone.utc),
        "input_path": f"s3://{S3_RAW_BUCKET}/events/{S3_DATE}/events_raw.parquet",
        "output_path": f"s3://{S3_PROCESSED_BUCKET}/events/{S3_DATE}/",
    }
}

NEXTFLOW_STEPS = {
    NEXTFLOW_RUN_ID: [
        {
            "step_name": "validate_input",
            "status": "COMPLETED",
            "started_at": datetime(2026, 1, 12, 23, 50, 0, tzinfo=timezone.utc),
            "ended_at": datetime(2026, 1, 12, 23, 52, 0, tzinfo=timezone.utc),
        },
        {
            "step_name": "transform",
            "status": "COMPLETED",
            "started_at": datetime(2026, 1, 12, 23, 52, 0, tzinfo=timezone.utc),
            "ended_at": datetime(2026, 1, 13, 0, 5, 0, tzinfo=timezone.utc),
        },
        {
            "step_name": "finalize",
            "status": "FAILED",
            "started_at": datetime(2026, 1, 13, 0, 5, 0, tzinfo=timezone.utc),
            "ended_at": datetime(2026, 1, 13, 0, 10, 0, tzinfo=timezone.utc),
            "error": "S3 permission denied writing _SUCCESS marker",
        },
    ]
}

NEXTFLOW_LOGS = {
    (NEXTFLOW_RUN_ID, "finalize"): """
2026-01-13 00:05:01 INFO  Starting finalize step
2026-01-13 00:05:02 INFO  Verifying output file exists: events_processed.parquet
2026-01-13 00:05:03 INFO  Output file verified successfully
2026-01-13 00:05:04 INFO  Attempting to write _SUCCESS marker
2026-01-13 00:05:05 ERROR S3 PutObject failed: AccessDenied
2026-01-13 00:05:05 ERROR IAM role missing s3:PutObject permission for tracer-processed-data/events/2026-01-13/_SUCCESS
2026-01-13 00:10:00 ERROR Finalize step failed after 5 retries
""".strip()
}

# Warehouse Mock Data
WAREHOUSE_TABLES = {
    "events_fact": {
        "table_name": "events_fact",
        "schema": "analytics",
        "last_updated": datetime(2026, 1, 12, 0, 15, 0, tzinfo=timezone.utc),  # Stale!
        "row_count": 50_000_000,
        "expected_update_interval_hours": 1,
    }
}

# Service B (Loader) Mock Data
LOADER_STATUS = {
    "events_loader": {
        "loader_name": "events_loader",
        "status": "WAITING",
        "target_table": "events_fact",
        "waiting_for": "_SUCCESS marker at s3://tracer-processed-data/events/2026-01-13/_SUCCESS",
        "last_check": datetime(2026, 1, 13, 2, 10, 0, tzinfo=timezone.utc),
        "checks_since_last_success": 24,
    }
}

