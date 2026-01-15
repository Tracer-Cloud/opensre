# Incident Report: events_fact Freshness SLA Breach

> **View Investigation in Tracer:** [https://staging.tracer.cloud/tracer-bioinformatics/investigations/cabac2de-f4e1-4177-8386-bc053a5bf6fe](https://staging.tracer.cloud/tracer-bioinformatics/investigations/cabac2de-f4e1-4177-8386-bc053a5bf6fe)

## Summary
* Pipeline aws_batch_tests failed after 43.6 minutes of execution
* AWS Batch job killed due to OutOfMemoryError despite 700GB RAM allocation
* Container memory usage exceeded available resources on g6e.24xlarge instance
* Missing _SUCCESS marker caused DataFreshnessSLABreach for events_fact table

## Evidence from Tracer

### Pipeline Run Details
| Field | Value |
|-------|-------|
| Pipeline | `aws_batch_tests` |
| Run Name | `velvet-bear-910` |
| Status | **Failed** [FAILED] |
| User | michele@tracer.cloud |
| Team | Oncology |
| Cost | $12.58 |
| Instance | g6e.24xlarge |
| Max RAM | 710.7 GB |

### AWS Batch Job Failure
- Failed jobs: 1
- **Failure reason**: `OutOfMemoryError: Container killed due to memory usage`

### S3 State
- Bucket: `tracer-logs`
- `_SUCCESS` marker: **missing**

## Root Cause Analysis
Confidence: 95%

* Pipeline aws_batch_tests failed after 43.6 minutes of execution
* AWS Batch job killed due to OutOfMemoryError despite 700GB RAM allocation
* Container memory usage exceeded available resources on g6e.24xlarge instance
* Missing _SUCCESS marker caused DataFreshnessSLABreach for events_fact table

## Recommended Actions
1. [View failed job in Tracer dashboard](https://staging.tracer.cloud/tracer-bioinformatics/investigations/cabac2de-f4e1-4177-8386-bc053a5bf6fe)
2. **Increase memory allocation** - job was killed due to OutOfMemoryError
3. Consider using a larger instance type with more RAM
4. Rerun pipeline after fixing resource allocation
