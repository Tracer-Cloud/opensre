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

1. **Orchestrator (ECS Flink ML Task)**
   A downstream ML feature engineering job fails while validating event schema.

2. **Task Logs (CloudWatch)**
   The agent retrieves execution logs and stack traces for the failed ML pipeline.

3. **Input Data Store (S3 – landing)**
   From the logs, the agent identifies the S3 object used as input and inspects event schema.

4. **Schema Validation**
   The agent detects a schema mismatch in ML events (missing event_id field).

5. **Data Lineage (S3 metadata)**
   The agent traces the event stream origin using metadata and correlation IDs.

6. **Upstream Compute (Trigger Lambda)**
   The agent retrieves the Lambda code and recent invocation context responsible for event ingestion.

7. **External Dependency (Mock Event Stream API)** → **This is the goal**
   The agent identifies the external event stream API and inspects the schema change that broke the ML pipeline.

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
Mock Event Stream API (schema change v2.0, removes event_id)
    ↓
Trigger Lambda (event ingestion + audit trail)
    ↓
S3 Landing Bucket (raw ML events + metadata)
    ↓
ECS Flink Task (ML Feature Engineering Pipeline)
    ↓
DomainError: Missing fields ['event_id'] (breaks feature deduplication)
    ↓
CloudWatch Logs (structured error with correlation_id)
    ↓
Agent investigates → Root cause: External event stream schema change
```

### Key Components

| Component | Purpose |
|-----------|---------|
| Mock Event Stream API | Simulates upstream ML event provider with schema versioning |
| Trigger Lambda | Ingests events and launches ECS Flink ML task |
| S3 Landing Bucket | Stores raw ML events with audit trail |
| ECS Flink Task | ML feature engineering pipeline with schema validation |
| S3 Processed Bucket | Stores feature-engineered output for ML models |
| CloudWatch Logs | Captures all pipeline execution logs |

## Differences from Prefect Test Case

| Aspect | Prefect | Flink |
|--------|---------|-------|
| Execution | Long-running service | One-shot batch task |
| Trigger | Prefect work pool | ECS RunTask API |
| Container | `prefecthq/prefect:3-python3.11` | `python:3.11-slim` + boto3 |
| State | Prefect server (SQLite) | Stateless |
| Complexity | Higher (server + worker) | Lower (single container) |
| Deploy Time | ~3-5 minutes | ~60-90 seconds |
