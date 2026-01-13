# Incident Report: events_fact Freshness SLA Breach

## Summary
• Pipeline processed data successfully and created output parquet file
• Finalize step failed when attempting to write _SUCCESS marker to S3
• IAM role lacks s3:PutObject permission for _SUCCESS marker path
• Downstream systems cannot detect job completion without _SUCCESS marker

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

• Pipeline processed data successfully and created output parquet file
• Finalize step failed when attempting to write _SUCCESS marker to S3
• IAM role lacks s3:PutObject permission for _SUCCESS marker path
• Downstream systems cannot detect job completion without _SUCCESS marker

## Recommended Actions
1. Grant Nextflow IAM role `s3:PutObject` permission on the `_SUCCESS` path
2. Rerun the Nextflow finalize step
3. Monitor Service B loader for successful pickup
