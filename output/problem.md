# Incident Report: events_fact Freshness SLA Breach

## Summary
* Pipeline aws_batch_tests (velvet-bear-910) failed after 43.6 minutes
* AWS Batch job failed with OutOfMemoryError: Container killed due to memory usage
* Job exceeded 700GB RAM allocation on g6e.24xlarge instance
* Missing _SUCCESS marker prevented events_fact table updates, causing SLA breach

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

* Pipeline aws_batch_tests (velvet-bear-910) failed after 43.6 minutes
* AWS Batch job failed with OutOfMemoryError: Container killed due to memory usage
* Job exceeded 700GB RAM allocation on g6e.24xlarge instance
* Missing _SUCCESS marker prevented events_fact table updates, causing SLA breach

## Recommended Actions
1. Review failed job in Tracer dashboard at https://staging.tracer.cloud
2. **Increase memory allocation** - job was killed due to OutOfMemoryError
3. Consider using a larger instance type with more RAM
4. Rerun pipeline after fixing resource allocation
