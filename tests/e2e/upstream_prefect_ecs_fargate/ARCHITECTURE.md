# Prefect ECS Fargate Test Case - Architecture

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
│                    ┌───────────────┼───────────────┐                        │
│                    │               │               │                        │
│                    ▼               ▼               ▼                        │
│         ┌──────────────┐  ┌───────────────┐  ┌──────────────┐             │
│         │ External API │  │  S3 Landing   │  │  S3 Audit    │             │
│         │   (Mock)     │  │    Bucket     │  │    Object    │             │
│         │              │  │               │  │              │             │
│         │ GET /data    │  │ ingested/     │  │ audit/       │             │
│         └──────┬───────┘  │ data.json     │  │ {id}.json    │             │
│                │          └───────┬───────┘  └──────────────┘             │
│                │                  │                                         │
│                │                  │ ┌─ S3 Metadata ────────────┐          │
│                │                  │ │ - correlation_id          │          │
│                └─────────────────►│ │ - audit_key (link)        │          │
│                  API Response     │ │ - schema_version          │          │
│                  (JSON)           │ │ - source: trigger_lambda  │          │
│                                   │ └───────────────────────────┘          │
│                                   │                                         │
│                                   ▼                                         │
│                        ┌───────────────────────┐                            │
│                        │   ECS Fargate Task    │                            │
│                        │  (Prefect Server +    │                            │
│                        │   Worker Process)     │                            │
│                        │                       │                            │
│                        │  ┌─────────────────┐ │                            │
│                        │  │ Prefect Server  │ │                            │
│                        │  │ (SQLite State)  │ │                            │
│                        │  │ Port: 4200      │ │                            │
│                        │  └────────┬────────┘ │                            │
│                        │           │          │                            │
│                        │  ┌────────▼────────┐ │                            │
│                        │  │ Prefect Worker  │ │                            │
│                        │  │ (Process Pool)  │ │                            │
│                        │  └────────┬────────┘ │                            │
│                        └───────────┼──────────┘                            │
│                                    │                                         │
│                                    ▼                                         │
│                        ┌───────────────────────┐                            │
│                        │  Prefect Flow (ETL)   │                            │
│                        │ upstream_downstream_  │                            │
│                        │      pipeline         │                            │
│                        │                       │                            │
│                        │  ┌────────────────┐  │                            │
│                        │  │ 1. Extract     │  │                            │
│                        │  │    (Read S3)   │  │                            │
│                        │  └────────┬───────┘  │                            │
│                        │           │          │                            │
│                        │  ┌────────▼───────┐  │                            │
│                        │  │ 2. Transform   │  │                            │
│                        │  │   (Validate +  │  │                            │
│                        │  │    Process)    │  │                            │
│                        │  └────────┬───────┘  │                            │
│                        │           │          │                            │
│                        │  ┌────────▼───────┐  │                            │
│                        │  │ 3. Load        │  │                            │
│                        │  │   (Write S3)   │  │                            │
│                        │  └────────┬───────┘  │                            │
│                        └───────────┼──────────┘                            │
│                                    │                                         │
│                                    ▼                                         │
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
│                        │  /ecs/tracer-prefect  │                            │
│                        │                       │                            │
│                        │  - Flow execution     │                            │
│                        │  - Task logs          │                            │
│                        │  - Error traces       │                            │
│                        │  - Audit events       │                            │
│                        └───────────────────────┘                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Component Breakdown

### 1. **Trigger Lambda** (`trigger_lambda/handler.py`)
**Purpose**: Ingestion orchestrator
**Responsibilities**:
- Receives HTTP POST requests via API Gateway
- Fetches data from Mock External Vendor API
- Writes audit payload to S3 (request/response tracking)
- Writes ingested data to S3 Landing Bucket with metadata
- Returns correlation_id and S3 location

**Environment Variables**:
- `LANDING_BUCKET`: S3 bucket for raw data
- `PROCESSED_BUCKET`: S3 bucket for transformed data
- `PREFECT_API_URL`: Prefect server endpoint
- `EXTERNAL_API_URL`: Mock external API endpoint

### 2. **Mock External Vendor API** (`mock_api_lambda`)
**Purpose**: Simulates upstream data source
**Location**: Shared from `upstream_lambda/pipeline_code/external_vendor_api/handler.py`
**Capabilities**:
- `GET /data`: Returns JSON data
- `POST /config`: Configure schema changes (for testing failures)
- `GET /health`: Health check endpoint
- `GET /config`: Get current configuration
- Can inject schema changes to trigger validation errors

**Note**: This Lambda code is reused from the upstream_lambda test case to maintain consistency across test scenarios and avoid code duplication.

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

### 4. **ECS Fargate Service**
**Purpose**: Prefect workflow orchestration platform
**Components**:
- **Prefect Server**: API and UI (port 4200)
- **Prefect Worker**: Executes flows from work pool
- **Container**: `prefecthq/prefect:3-python3.11`

**Deployment**:
- Runs in default VPC public subnet
- Assigned public IP
- SQLite for state (ephemeral)
- CloudWatch Logs integration

### 5. **Prefect Flow** (`pipeline_code/prefect_flow/flow.py`)
**Name**: `upstream_downstream_pipeline`
**Tasks**:

1. **extract_data** (retries: 2)
   - Reads JSON from S3 landing bucket
   - Extracts correlation_id from metadata
   - Returns raw payload

2. **transform_data**
   - Validates required fields (`customer_id`, `order_id`, `amount`, `timestamp`)
   - Transforms records to domain model
   - Raises `PipelineError` on validation failure

3. **load_data** (retries: 2)
   - Writes processed data to S3 processed bucket
   - Adds metadata linking back to source
   - Preserves correlation_id for tracing

**Error Handling**:
- Catches `PipelineError` and fires alert
- Logs structured events for investigation
- Includes correlation_id in all logs

### 6. **S3 Processed Bucket**
**Purpose**: Transformed data storage
**Contents**:
- `processed/{timestamp}/data.json`: Validated and transformed records

**Metadata**:
- `correlation_id`: Trace back to ingestion
- `source_key`: Original S3 object key

### 7. **CloudWatch Logs**
**Log Group**: `/ecs/tracer-prefect`
**Content**:
- Prefect server startup logs
- Worker execution logs
- Flow run logs (extract/transform/load)
- Error stack traces
- Structured JSON events

## Data Flow (Happy Path)

```
1. HTTP POST /trigger
   └─> API Gateway
       └─> Trigger Lambda
           ├─> GET Mock External API /data
           │   └─> Returns: {"data": [...], "meta": {...}}
           │
           ├─> PUT S3 audit/{id}.json (API request/response)
           │
           └─> PUT S3 ingested/{timestamp}/data.json
               └─> Metadata: correlation_id, audit_key, schema_version

2. Prefect Worker (polls work pool)
   └─> Executes flow: upstream_downstream_pipeline
       ├─> Task: extract_data
       │   └─> GET S3 ingested/{timestamp}/data.json
       │
       ├─> Task: transform_data
       │   └─> Validate schema (customer_id, order_id, amount, timestamp)
       │   └─> Transform to ProcessedRecord objects
       │
       └─> Task: load_data
           └─> PUT S3 processed/{timestamp}/data.json
               └─> Metadata: correlation_id, source_key

3. CloudWatch Logs
   └─> All flow execution logs captured in /ecs/tracer-prefect
```

## Data Flow (Failure Path - Schema Mismatch)

```
1. HTTP POST /trigger?inject_error=true
   └─> API Gateway
       └─> Trigger Lambda
           ├─> POST Mock External API /config {"inject_schema_change": true}
           │
           ├─> GET Mock External API /data
           │   └─> Returns: {"data": [{order_id, amount}], ...}  ❌ Missing customer_id
           │
           ├─> PUT S3 audit/{id}.json (captures schema change)
           │
           └─> PUT S3 ingested/{timestamp}/data.json
               └─> Metadata: schema_change_injected=True

2. Prefect Worker
   └─> Executes flow: upstream_downstream_pipeline
       ├─> Task: extract_data ✓
       │   └─> GET S3 ingested/{timestamp}/data.json
       │
       ├─> Task: transform_data ❌ FAILS
       │   └─> Validation error: Missing required field 'customer_id'
       │   └─> Raises PipelineError
       │
       └─> Error Handler
           ├─> Logs error with correlation_id
           ├─> Fires pipeline alert (if configured)
           └─> Re-raises exception

3. CloudWatch Logs
   └─> Error trace includes:
       ├─> Missing field: customer_id
       ├─> correlation_id for tracing
       ├─> S3 input location
       └─> Stack trace
```

## Investigation Path (What Agent Should Detect)

When investigating a pipeline failure, the Tracer Agent should:

### 1. **Start**: Orchestrator Logs (CloudWatch)
- Retrieve flow execution logs from `/ecs/tracer-prefect`
- Identify failed task: `transform_data`
- Extract error: `Missing required field 'customer_id'`
- Extract `correlation_id` from logs

### 2. **Input Data Store (S3 Landing)**
- Get S3 object path from logs: `s3://landing-bucket/ingested/{timestamp}/data.json`
- Inspect object content and metadata
- Detect schema version mismatch
- Find `audit_key` in metadata

### 3. **Schema Validation**
- Compare actual fields vs. required fields
- Identify missing field: `customer_id`
- Confirm schema mismatch cause

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
External vendor API changed schema from v1.0 to v2.0, removing `customer_id` field, causing downstream validation failure in Prefect flow.

## AWS Resources

| Resource Type | Name/ID | Purpose |
|---------------|---------|---------|
| ECS Cluster | `tracer-prefect-cluster` | Hosts Prefect server + worker |
| ECS Task Definition | `PrefectTaskDef` | Fargate container spec (512 CPU, 1024 MB) |
| CloudWatch Log Group | `/ecs/tracer-prefect` | Prefect execution logs |
| S3 Bucket | `landing-bucket` | Raw ingested data |
| S3 Bucket | `processed-bucket` | Transformed data |
| Lambda | `TriggerLambda` | Ingestion handler |
| Lambda | `MockApiLambda` | External vendor API simulator |
| API Gateway | `tracer-prefect-trigger` | HTTP trigger endpoint |
| API Gateway | `MockExternalApi` | Mock vendor API endpoint |

## Key Differences from Lambda Test Case

| Aspect | Lambda Test Case | Prefect ECS Test Case |
|--------|------------------|----------------------|
| Orchestrator | AWS Lambda (Mock DAG) | Prefect on ECS Fargate |
| Execution | Event-driven (S3 trigger) | Worker polling work pool |
| State Management | Stateless | Prefect server (SQLite) |
| Retry Logic | Lambda retries | Prefect task retries |
| Logging | CloudWatch Logs (Lambda) | CloudWatch Logs (ECS) |
| Complexity | Simple Lambda function | Multi-task workflow |
| Testing Trigger | Direct Lambda invocation | HTTP API → Lambda → S3 → Prefect |

## Test Scenarios

### Happy Path
```bash
POST /trigger
→ External API returns valid data (v1.0 schema)
→ Flow completes successfully
→ Data written to processed bucket
```

### Failure Path
```bash
POST /trigger?inject_error=true
→ External API returns data with schema change (v2.0, missing customer_id)
→ transform_data task fails validation
→ PipelineError raised
→ Alert fired (if configured)
→ Error logged to CloudWatch
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
