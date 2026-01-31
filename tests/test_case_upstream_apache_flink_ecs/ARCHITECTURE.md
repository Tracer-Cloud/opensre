# Apache Flink ECS Test Case - Architecture

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          HTTP Request (Trigger)                             │
│                                    │                                         │
│                                    ▼                                         │
│                        ┌───────────────────────┐                            │
│                        │   API Gateway (HTTP)  │                            │
│                        │   /trigger endpoint   │                            │
│                        └───────────┬───────────┘                            │
│                                    │                                         │
│                                    ▼                                         │
│                        ┌───────────────────────┐                            │
│                        │   Trigger Lambda      │                            │
│                        │  (Ingestion Handler)  │                            │
│                        └───────────┬───────────┘                            │
│                                    │                                         │
│                    ┌───────────────┼───────────────┬──────────────┐        │
│                    │               │               │              │        │
│                    ▼               ▼               ▼              ▼        │
│         ┌──────────────┐  ┌───────────────┐  ┌──────────────┐ ┌─────────┐ │
│         │ External API │  │  S3 Landing   │  │  S3 Audit    │ │   ECS   │ │
│         │   (Mock)     │  │    Bucket     │  │    Object    │ │ RunTask │ │
│         │              │  │               │  │              │ │   API   │ │
│         │ GET /data    │  │ ingested/     │  │ audit/       │ │         │ │
│         └──────┬───────┘  │ data.json     │  │ {id}.json    │ └────┬────┘ │
│                │          └───────┬───────┘  └──────────────┘      │      │
│                │                  │                                 │      │
│                │                  │ ┌─ S3 Metadata ────────────┐   │      │
│                │                  │ │ - correlation_id          │   │      │
│                └─────────────────►│ │ - audit_key (link)        │   │      │
│                  API Response     │ │ - schema_version          │   │      │
│                  (JSON)           │ │ - source: trigger_lambda  │   │      │
│                                   │ └───────────────────────────┘   │      │
│                                   │                                 │      │
│                                   ▼                                 │      │
│                        ┌───────────────────────┐◄───────────────────┘      │
│                        │   ECS Fargate Task    │                           │
│                        │  (PyFlink Batch Job)  │                           │
│                        │                       │                           │
│                        │  ┌─────────────────┐ │                            │
│                        │  │  PyFlink Job    │ │                            │
│                        │  │  (main.py)      │ │                            │
│                        │  │                 │ │                            │
│                        │  │  1. Read S3     │ │                            │
│                        │  │  2. Validate    │ │                            │
│                        │  │  3. Transform   │ │                            │
│                        │  │  4. Write S3    │ │                            │
│                        │  └────────┬────────┘ │                            │
│                        └───────────┼──────────┘                            │
│                                    │                                        │
│                                    ▼                                        │
│                        ┌───────────────────────┐                            │
│                        │  S3 Processed Bucket  │                            │
│                        │  processed/data.json  │                            │
│                        │                       │                            │
│                        │  + S3 Metadata:       │                            │
│                        │    - correlation_id   │                            │
│                        │    - source_key (link)│                            │
│                        └───────────────────────┘                            │
│                                                                              │
│                        ┌───────────────────────┐                            │
│                        │  CloudWatch Logs      │                            │
│                        │  /ecs/tracer-flink    │                            │
│                        │                       │                            │
│                        │  - Job execution      │                            │
│                        │  - Validation logs    │                            │
│                        │  - Error traces       │                            │
│                        │  - Audit events       │                            │
│                        └───────────────────────┘                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Component Breakdown

### 1. **Trigger Lambda** (`trigger_lambda/handler.py`)
**Purpose**: Ingestion orchestrator + ECS task launcher
**Responsibilities**:
- Receives HTTP POST requests via API Gateway
- Fetches data from Mock External Vendor API
- Writes audit payload to S3 (request/response tracking)
- Writes ingested data to S3 Landing Bucket with metadata
- Starts ECS Flink task via RunTask API
- Returns correlation_id and task ARN

**Environment Variables**:
- `LANDING_BUCKET`: S3 bucket for raw data
- `PROCESSED_BUCKET`: S3 bucket for transformed data
- `EXTERNAL_API_URL`: Mock external API endpoint
- `ECS_CLUSTER`: Flink ECS cluster ARN
- `TASK_DEFINITION`: Flink task definition ARN
- `SUBNET_IDS`: VPC subnet IDs for Fargate tasks
- `SECURITY_GROUP_ID`: Security group for Fargate tasks

### 2. **Mock External Vendor API** (`mock_api_lambda`)
**Purpose**: Simulates upstream data source
**Location**: Shared from `tests/shared/external_vendor_api/handler.py`
**Capabilities**:
- `GET /data`: Returns JSON data
- `POST /config`: Configure schema changes (for testing failures)
- `GET /health`: Health check endpoint
- `GET /config`: Get current configuration
- Can inject schema changes to trigger validation errors

### 3. **S3 Landing Bucket**
**Purpose**: Raw data storage
**Contents**:
- `ingested/{timestamp}/data.json`: Raw API responses
- `audit/{correlation_id}.json`: Full request/response audit trail

**Metadata** (attached to objects):
- `correlation_id`: Unique identifier for tracking
- `audit_key`: Reference to audit payload
- `schema_version`: Data schema version
- `source`: Ingestion source identifier

### 4. **ECS Fargate Task (PyFlink)**
**Purpose**: Batch data processing
**Components**:
- **PyFlink Runtime**: Python batch job with domain validation
- **Batch Job**: One-shot execution (runs and exits)
- **Container**: `python:3.11-slim` with boto3

**Execution Model**:
- Triggered on-demand via ECS RunTask API
- Reads input from S3 landing bucket
- Validates schema, transforms data
- Writes output to S3 processed bucket
- Exits with status code (0 = success, non-zero = failure)

### 5. **PyFlink Job** (`pipeline_code/flink_job/main.py`)
**Name**: `tracer_flink_ml_feature_pipeline`
**Purpose**: Feature engineering for ML model consumption

**Steps**:

1. **Read Event Data**
   - Reads JSON from S3 landing bucket
   - Extracts correlation_id from S3 metadata
   - Returns raw event payload

2. **Validate & Engineer Features**
   - Validates required fields (`event_id`, `user_id`, `event_type`, `timestamp`)
   - Computes ML features from raw_features:
     * Normalized numerical features
     * One-hot encoded categorical features
     * Interaction features (value_per_second, avg_value_per_count)
     * Temporal features (is_weekend, hour_of_day)
   - Generates feature hash for versioning
   - Raises `DomainError` on validation failure

3. **Write Feature-Engineered Output**
   - Writes processed features to S3 processed bucket
   - Includes feature hash for ML model versioning
   - Adds metadata linking back to source event
   - Preserves correlation_id for tracing

**Error Handling**:
- Catches `DomainError` and logs structured error
- Includes correlation_id in all logs
- Exits with non-zero status on failure

### 6. **S3 Processed Bucket**
**Purpose**: Transformed data storage
**Contents**:
- `processed/{correlation_id}/data.json`: Validated and transformed records

**Metadata**:
- `correlation_id`: Trace back to ingestion
- `source_key`: Original S3 object key

### 7. **CloudWatch Logs**
**Log Group**: `/ecs/tracer-flink`
**Content**:
- Flink job startup logs
- Data processing logs
- Validation errors with correlation_id
- Stack traces on failure

## Data Flow (Happy Path)

```
1. HTTP POST /trigger
   └─> API Gateway
       └─> Trigger Lambda
           ├─> GET Mock External API /data
           │   └─> Returns: ML events with raw_features
           │
           ├─> PUT S3 audit/{id}.json (API request/response)
           │
           ├─> PUT S3 ingested/{timestamp}/data.json
           │   └─> Metadata: correlation_id, audit_key, schema_version
           │
           └─> ECS RunTask (Flink ML job)
               └─> Container starts with env vars:
                   - LANDING_BUCKET
                   - PROCESSED_BUCKET
                   - CORRELATION_ID
                   - S3_KEY

2. ECS Flink Task (ML Feature Engineering)
   └─> Executes: python main.py
       ├─> Read S3 ingested/{timestamp}/data.json
       │
       ├─> Validate event schema (event_id, user_id, event_type, timestamp)
       │
       ├─> Engineer ML features from raw_features:
       │   * Normalized values
       │   * One-hot encoded event types
       │   * Interaction features
       │   * Temporal features
       │
       ├─> Compute feature hash for versioning
       │
       └─> PUT S3 processed/{correlation_id}/data.json
           └─> Metadata: correlation_id, source_key, feature_hash

3. CloudWatch Logs
   └─> All job execution logs captured in /ecs/tracer-flink
```

## Data Flow (Failure Path - Schema Mismatch)

```
1. HTTP POST /trigger?inject_error=true
   └─> API Gateway
       └─> Trigger Lambda
           ├─> POST Mock External API /config {"inject_schema_change": true}
           │
           ├─> GET Mock External API /data
           │   └─> Returns: ML events without event_id  ❌ Missing event_id
           │
           ├─> PUT S3 audit/{id}.json (captures schema change)
           │
           ├─> PUT S3 ingested/{timestamp}/data.json
           │   └─> Metadata: schema_change_injected=True
           │
           └─> ECS RunTask (Flink ML job)

2. ECS Flink Task (ML Feature Engineering)
   └─> Executes: python main.py
       ├─> Read S3 ingested/{timestamp}/data.json ✓
       │
       ├─> Validate event schema ❌ FAILS
       │   └─> DomainError: Missing required field 'event_id'
       │       (Critical for ML feature deduplication)
       │
       └─> Task exits with status code 1

3. CloudWatch Logs
   └─> Error trace includes:
       ├─> [FLINK][ERROR] Schema validation failed: Missing fields ['event_id']
       ├─> correlation_id for tracing
       ├─> S3 input location
       └─> Stack trace
```

## Investigation Path (What Agent Should Detect)

When investigating a pipeline failure, the Tracer Agent should:

### 1. **Start**: ECS Task Logs (CloudWatch)
- Retrieve job execution logs from `/ecs/tracer-flink`
- Identify failed validation step
- Extract error: `Missing required field 'customer_id'`
- Extract `correlation_id` from logs

### 2. **Input Data Store (S3 Landing)**
- Get S3 object path from logs: `s3://landing-bucket/ingested/{timestamp}/data.json`
- Inspect object content and metadata
- Detect schema version mismatch
- Find `audit_key` in metadata

### 3. **Schema Validation**
- Compare actual fields vs. required fields
- Identify missing field: `event_id`
- Confirm schema mismatch cause (breaks ML feature deduplication)

### 4. **Data Lineage (S3 Metadata)**
- Read `correlation_id` from object metadata
- Read `audit_key` reference
- Trace origin to Trigger Lambda

### 5. **Upstream Compute (Trigger Lambda)**
- Retrieve Lambda code and configuration
- Get recent invocations using `correlation_id`
- Identify external API call in logs

### 6. **External Dependency (Audit Payload)** 🎯 **GOAL**
- Retrieve audit object: `s3://landing-bucket/audit/{correlation_id}.json`
- Inspect full request/response from external API
- Confirm external API returned data without `customer_id`
- Identify schema version change: `v1.0` → `v2.0`

### Root Cause
External event stream API changed schema from v1.0 to v2.0, removing `event_id` field (critical for ML feature deduplication and versioning), causing downstream validation failure in Flink ML feature engineering pipeline.

## AWS Resources (Deployed)

| Resource Type | Name/ID | Purpose |
|---------------|---------|---------|
| ECS Cluster | `tracer-flink-cluster` | Hosts Flink batch tasks |
| ECS Task Definition | `TracerFlinkEcsFlinkTaskDef` | Fargate container spec (512 CPU, 1024 MB, ARM64) |
| CloudWatch Log Group | `/ecs/tracer-flink` | Flink job execution logs |
| S3 Bucket | `tracerflinkecs-landingbucket23fe90fb-ztviw7xibnx7` | Raw ingested data |
| S3 Bucket | `tracerflinkecs-processedbucketde59930c-bxdsoonzx2pq` | Transformed data |
| Lambda | `TriggerLambda` | Ingestion handler + ECS launcher |
| Lambda | `MockApiLambda` | External vendor API simulator |
| API Gateway | `https://pbjh63udyc.execute-api.us-east-1.amazonaws.com/prod/` | HTTP trigger endpoint |
| API Gateway | `https://ff1aspehx9.execute-api.us-east-1.amazonaws.com/prod/` | Mock vendor API endpoint |

## Key Differences from Prefect Test Case

| Aspect | Prefect ECS Test Case | Flink ECS Test Case |
|--------|----------------------|---------------------|
| Orchestrator | Prefect 3.x (server + worker) | PyFlink (batch job) |
| Execution Model | Long-running service | One-shot task |
| Trigger | Prefect API / work pool | ECS RunTask API |
| State Management | Prefect server (SQLite) | Stateless |
| Container | `prefecthq/prefect:3-python3.11` | `python:3.11-slim` + boto3 |
| Log Group | `/ecs/tracer-prefect` | `/ecs/tracer-flink` |
| Cluster | `tracer-prefect-cluster` | `tracer-flink-cluster` |
| Deploy Time | ~3-5 minutes | ~60-90 seconds |
| Complexity | Higher (server + worker) | Lower (single container) |

## Test Scenarios

### Happy Path
```bash
POST /trigger
→ External API returns valid data (v1.0 schema)
→ ECS Flink task processes successfully
→ Data written to processed bucket
→ Task exits with code 0
```

### Failure Path
```bash
POST /trigger?inject_error=true
→ External API returns data with schema change (v2.0, missing customer_id)
→ ECS Flink task fails validation
→ DomainError raised
→ Error logged to CloudWatch
→ Task exits with code 1
```

## Tracer Agent Investigation Capabilities

The agent should demonstrate:

1. ✅ **CloudWatch Log Analysis**: Parse ECS task logs
2. ✅ **S3 Object Inspection**: Read landing/processed data
3. ✅ **S3 Metadata Tracing**: Follow audit_key references
4. ✅ **Lambda Code Analysis**: Inspect Trigger Lambda
5. ✅ **Lambda Invocation Logs**: Find recent executions
6. ✅ **External API Audit**: Retrieve and analyze vendor request/response
7. ✅ **Schema Comparison**: Detect schema version mismatches
8. ✅ **Root Cause Identification**: Trace failure to external API schema change

## Validated Test Results (2026-01-31)

| Metric | Value |
|--------|-------|
| Confidence | 86% |
| Validity | 88% |
| Checks Passed | 5/5 |

### Validation Checks

| Check | Status | Evidence |
|-------|--------|----------|
| Flink logs retrieved | ✅ PASS | CloudWatch `/ecs/tracer-flink` |
| S3 input data inspected | ✅ PASS | Landing bucket object + metadata |
| Audit trail traced | ✅ PASS | `audit/{correlation_id}.json` |
| External API identified | ✅ PASS | `external_api_url` in audit payload |
| Schema change detected | ✅ PASS | `schema_version: 2.0`, missing `customer_id` |

### Sample RCA Output

```
*Validated Claims (Supported by Evidence):*
• Input data contains schema version 2.0 with a truncated breaking change notification
• The S3 metadata explicitly indicates a schema change was injected
• The schema change introduced breaking modifications to customer-related fields
• The failure during module import suggests the pipeline lacks proper schema validation

*Data Lineage Flow (Evidence-Based)*
1. S3 Landing → Pipeline Executor (ECS Flink Task)

*Confidence:* 86%
*Validity Score:* 88% (7/7 validated)
```
