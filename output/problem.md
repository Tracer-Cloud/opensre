# Incident Report: events_fact Freshness SLA Breach

## Summary
• Nextflow pipeline processed data successfully (events_processed.parquet exists)
• Pipeline failed at finalization due to S3 AccessDenied error
• IAM role lacks s3:PutObject permission for _SUCCESS marker creation
• Downstream systems interpret missing _SUCCESS marker as failed/incomplete job

## Evidence

### S3 State
- Bucket: `tracer-processed-data`
- Prefix: `events/2026-01-13/`
- `_SUCCESS` marker: **missing**

### Nextflow Pipeline
- Pipeline: `events-etl`
- Finalize status: `FAILED`

## Root Cause Analysis
Confidence: 95%

• Nextflow pipeline processed data successfully (events_processed.parquet exists)
• Pipeline failed at finalization due to S3 AccessDenied error
• IAM role lacks s3:PutObject permission for _SUCCESS marker creation
• Downstream systems interpret missing _SUCCESS marker as failed/incomplete job

## Recommended Actions
1. Grant Nextflow IAM role `s3:PutObject` permission on the `_SUCCESS` path
2. Rerun the Nextflow finalize step
3. Monitor Service B loader for successful pickup
