# Airflow ECS Fargate Test Case - Architecture

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
│                        │   (Airflow 3.1.6)     │                            │
│                        │                       │                            │
│                        │  ┌─────────────────┐ │                            │
│                        │  │ Airflow API     │ │                            │
│                        │  │ Port: 8080      │ │                            │
│                        │  └────────┬────────┘ │                            │
│                        │           │          │                            │
│                        │  ┌────────▼────────┐ │                            │
│                        │  │ Scheduler +     │ │                            │
│                        │  │ Task Runner     │ │                            │
│                        │  └────────┬────────┘ │                            │
│                        └───────────┼──────────┘                            │
│                                    │                                         │
│                                    ▼                                         │
│                        ┌───────────────────────┐                            │
│                        │  Airflow DAG (ETL)    │                            │
│                        │ upstream_downstream_  │                            │
│                        │   pipeline_airflow    │                            │
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
│                        │  /ecs/tracer-airflow  │                            │
│                        │                       │                            │
│                        │  - DAG run logs       │                            │
│                        │  - Task logs          │                            │
│                        │  - Error traces       │                            │
│                        └───────────────────────┘                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Investigation Path (What Agent Should Detect)

1. **Start**: Airflow logs (CloudWatch)
   - Identify failed task in `transform_data`
   - Extract error: missing required field
   - Extract `correlation_id` from log context

2. **Input Data Store (S3 Landing)**
   - Inspect `s3://landing/ingested/{timestamp}/data.json`
   - Read metadata `audit_key` and `schema_version`

3. **Schema Validation**
   - Confirm required field missing (e.g., `event_id`)
   - Validate mismatch cause

4. **Data Lineage (S3 Metadata)**
   - Follow `audit_key` to audit object
   - Trace origin to Trigger Lambda

5. **External Dependency (Audit Payload)**
   - Inspect vendor request/response
   - Confirm external API schema change triggered failure

## Root Cause

External vendor API changed schema (removed `event_id`), which caused the Airflow
transform task to fail validation.
# Airflow ECS Fargate Test Case - Architecture

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
│                        │   (Airflow 3.1.6)     │                            │
│                        │                       │                            │
│                        │  ┌─────────────────┐ │                            │
│                        │  │ Airflow API     │ │                            │
│                        │  │ Port: 8080      │ │                            │
│                        │  └────────┬────────┘ │                            │
│                        │           │          │                            │
│                        │  ┌────────▼────────┐ │                            │
│                        │  │ Scheduler +     │ │                            │
│                        │  │ Task Runner     │ │                            │
│                        │  └────────┬────────┘ │                            │
│                        └───────────┼──────────┘                            │
│                                    │                                         │
│                                    ▼                                         │
│                        ┌───────────────────────┐                            │
│                        │  Airflow DAG (ETL)    │                            │
│                        │ upstream_downstream_  │                            │
│                        │   pipeline_airflow    │                            │
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
│                        │  /ecs/tracer-airflow  │                            │
│                        │                       │                            │
│                        │  - DAG run logs       │                            │
│                        │  - Task logs          │                            │
│                        │  - Error traces       │                            │
│                        └───────────────────────┘                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Investigation Path (What Agent Should Detect)

1. **Start**: Airflow logs (CloudWatch)
   - Identify failed task in `transform_data`
   - Extract error: missing required field
   - Extract `correlation_id` from log context

2. **Input Data Store (S3 Landing)**
   - Inspect `s3://landing/ingested/{timestamp}/data.json`
   - Read metadata `audit_key` and `schema_version`

3. **Schema Validation**
   - Confirm required field missing (e.g., `event_id`)
   - Validate mismatch cause

4. **Data Lineage (S3 Metadata)**
   - Follow `audit_key` to audit object
   - Trace origin to Trigger Lambda

5. **External Dependency (Audit Payload)**
   - Inspect vendor request/response
   - Confirm external API schema change triggered failure

## Root Cause

External vendor API changed schema (removed `event_id`), which caused the Airflow
transform task to fail validation.
