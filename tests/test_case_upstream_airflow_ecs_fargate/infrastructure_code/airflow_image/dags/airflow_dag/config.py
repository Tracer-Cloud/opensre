"""Configuration for the Airflow DAG pipeline."""

import os

LANDING_BUCKET = os.getenv("LANDING_BUCKET", "landing-bucket")
PROCESSED_BUCKET = os.getenv("PROCESSED_BUCKET", "processed-bucket")

PIPELINE_NAME = "upstream_downstream_pipeline_airflow"
REQUIRED_FIELDS = ["event_id", "user_id", "timestamp", "event_type", "raw_features"]
