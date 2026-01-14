# Incident Report: events_fact Freshness SLA Breach

## Summary
• Pipeline successfully processed data and created output parquet file
• Finalize step failed due to S3 AccessDenied when writing _SUCCESS marker
• IAM role lacks s3:PutObject permission for _SUCCESS file in S3 path
• Downstream systems cannot detect completion, triggering freshness SLA breach

## Evidence

### S3 State
- Bucket: `tracer-logs`
- Prefix: `events/2026-01-13/`
- `_SUCCESS` marker: **missing**

### Nextflow Pipeline
- Pipeline: `events-etl`
- Finalize status: `FAILED`

## Root Cause Analysis
Confidence: 95%

• Pipeline successfully processed data and created output parquet file
• Finalize step failed due to S3 AccessDenied when writing _SUCCESS marker
• IAM role lacks s3:PutObject permission for _SUCCESS file in S3 path
• Downstream systems cannot detect completion, triggering freshness SLA breach

## Recommended Actions
1. Grant Nextflow IAM role `s3:PutObject` permission on the `_SUCCESS` path
2. Rerun the Nextflow finalize step
3. Monitor Service B loader for successful pickup
