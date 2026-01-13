# Incident Report: DataFreshnessSLABreach

## Summary
- **Severity:** critical
- **Affected Table:** events_fact
- **Confidence:** 95%

## Root Cause
The Nextflow finalize step failed due to S3 AccessDenied error. The IAM role is missing s3:PutObject permission, which prevented the _SUCCESS marker from being written. Service B loader is blocked.

## Evidence Collected

### S3 Check
- _SUCCESS marker exists: False
- Files in output prefix: 1

### Nextflow Check
- Finalize step status: FAILED
- Logs available: True

## Recommended Actions
1. **[CRITICAL]** Fix IAM permissions for s3:PutObject on tracer-processed-data bucket
2. **[HIGH]** Rerun Nextflow finalize step to write _SUCCESS marker
3. **[MEDIUM]** Add alerting on IAM permission failures

## Logs
```
2026-01-13 00:05:01 INFO  Starting finalize step
2026-01-13 00:05:02 INFO  Verifying output file exists: events_processed.parquet
2026-01-13 00:05:03 INFO  Output file verified successfully
2026-01-13 00:05:04 INFO  Attempting to write _SUCCESS marker
2026-01-13 00:05:05 ERROR S3 PutObject failed: AccessDenied
2026-01-13 00:05:05 ERROR IAM role missing s3:PutObject permission for tracer-processed-data/events/2026-01-13/_SUCCESS
2026-01-13 00:10:00 ERROR Finalize step failed after 5 retries
```
