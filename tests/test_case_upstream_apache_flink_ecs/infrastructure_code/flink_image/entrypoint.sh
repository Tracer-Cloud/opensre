#!/bin/bash
set -e

echo "[FLINK] Starting batch job..."
echo "[FLINK] LANDING_BUCKET=$LANDING_BUCKET"
echo "[FLINK] PROCESSED_BUCKET=$PROCESSED_BUCKET"
echo "[FLINK] CORRELATION_ID=$CORRELATION_ID"
echo "[FLINK] S3_KEY=$S3_KEY"

# Run the PyFlink batch job
python -m flink_job.main \
    --input-bucket "$LANDING_BUCKET" \
    --output-bucket "$PROCESSED_BUCKET" \
    --correlation-id "$CORRELATION_ID" \
    --s3-key "$S3_KEY"

echo "[FLINK] Batch job completed successfully"
