import os

LANDING_BUCKET = os.getenv("LANDING_BUCKET", "")
PROCESSED_BUCKET = os.getenv("PROCESSED_BUCKET", "")
S3_KEY = os.getenv("S3_KEY", "")
PIPELINE_RUN_ID = os.getenv("PIPELINE_RUN_ID", "default")

PIPELINE_NAME = "kubernetes_etl_pipeline"
REQUIRED_FIELDS = ["customer_id", "order_id", "amount", "timestamp"]


def staging_key(filename: str) -> str:
    return f"staging/{PIPELINE_RUN_ID}/{filename}"
