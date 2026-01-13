# RCA — events_fact freshness incident

**Analyzed by:** pipeline-agent
**Detected:** 02:13 UTC
**Confidence:** 0.95

## Conclusion

• Pipeline processed data successfully and created output parquet file
• Finalize step failed due to S3 AccessDenied error when writing _SUCCESS marker
• IAM role lacks s3:PutObject permission for _SUCCESS file in S3 bucket
• Downstream systems can't detect job completion, triggering SLA breach alert

## Evidence Chain

| Check | Result |
|-------|--------|
| Raw input file | Present in S3 |
| Processed output | `events_processed.parquet` written |
| Nextflow finalize | FAILED after 5 retries |
| `_SUCCESS` marker | Missing |
| Service B loader | Running, blocked on `_SUCCESS` |

## Actions

1. Grant Nextflow role `s3:PutObject` on `tracer-processed-data/events/2026-01-13/_SUCCESS`
2. Rerun Nextflow finalize step

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
