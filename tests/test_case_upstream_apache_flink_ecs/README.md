# Apache Flink ECS Test Case

**Status**: ✅ Deployed and Validated (2026-01-31)

## Test Results

| Metric | Value |
|--------|-------|
| Confidence | 86% |
| Validity | 88% |
| Checks Passed | 5/5 |

### Validation Checks

- ✅ Flink logs retrieved
- ✅ S3 input data inspected
- ✅ Audit trail traced
- ✅ External API identified
- ✅ Schema change detected

## What Should Be Detected

1. **Orchestrator (ECS Flink Task)**
   A downstream batch processing job fails while validating input data.

2. **Task Logs (CloudWatch)**
   The agent retrieves execution logs and stack traces for the failed job.

3. **Input Data Store (S3 – landing)**
   From the logs, the agent identifies the S3 object used as input and inspects its schema.

4. **Schema Validation**
   The agent detects a schema mismatch in the S3 input data (missing customer_id).

5. **Data Lineage (S3 metadata)**
   The agent traces the S3 object origin using metadata and correlation IDs.

6. **Upstream Compute (Trigger Lambda)**
   The agent retrieves the Lambda code and recent invocation context responsible for writing the S3 object.

7. **External Dependency (Mock External Vendor API)** → **This is the goal**
   The agent identifies the external API dependency and inspects the request/response payloads.

## Deployed Infrastructure

| Resource | Value |
|----------|-------|
| Trigger API | `https://pbjh63udyc.execute-api.us-east-1.amazonaws.com/prod/` |
| Mock API | `https://ff1aspehx9.execute-api.us-east-1.amazonaws.com/prod/` |
| Landing Bucket | `tracerflinkecs-landingbucket23fe90fb-ztviw7xibnx7` |
| Processed Bucket | `tracerflinkecs-processedbucketde59930c-bxdsoonzx2pq` |
| ECS Cluster | `tracer-flink-cluster` |
| Log Group | `/ecs/tracer-flink` |

## Quick Start

### Deploy Infrastructure

```bash
cd infrastructure_code
./deploy.sh
```

### Trigger Happy Path

```bash
curl -X POST "https://pbjh63udyc.execute-api.us-east-1.amazonaws.com/prod/trigger"
```

### Trigger Failure (Schema Change)

```bash
curl -X POST "https://pbjh63udyc.execute-api.us-east-1.amazonaws.com/prod/trigger?inject_error=true"
```

### Run Agent Investigation

```bash
cd ../../..
python -m tests.test_case_upstream_apache_flink_ecs.test_agent_e2e
```

### Destroy Infrastructure

```bash
cd infrastructure_code
./destroy.sh
```

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed architecture documentation.

### Failure Propagation Path

```
Mock External Vendor API (schema change v2.0, removes customer_id)
    ↓
Trigger Lambda (ingestion + audit trail)
    ↓
S3 Landing Bucket (raw data + metadata)
    ↓
ECS Flink Task (PyFlink batch job)
    ↓
DomainError: Missing fields ['customer_id']
    ↓
CloudWatch Logs (structured error with correlation_id)
    ↓
Agent investigates → Root cause: External API schema change
```

### Key Components

| Component | Purpose |
|-----------|---------|
| Mock External API | Simulates upstream data provider with schema versioning |
| Trigger Lambda | Ingests data and launches ECS Flink task |
| S3 Landing Bucket | Stores raw input data with audit trail |
| ECS Flink Task | Batch processing with schema validation |
| S3 Processed Bucket | Stores validated/transformed output |
| CloudWatch Logs | Captures all execution logs |

## Differences from Prefect Test Case

| Aspect | Prefect | Flink |
|--------|---------|-------|
| Execution | Long-running service | One-shot batch task |
| Trigger | Prefect work pool | ECS RunTask API |
| Container | `prefecthq/prefect:3-python3.11` | `python:3.11-slim` + boto3 |
| State | Prefect server (SQLite) | Stateless |
| Complexity | Higher (server + worker) | Lower (single container) |
| Deploy Time | ~3-5 minutes | ~60-90 seconds |
