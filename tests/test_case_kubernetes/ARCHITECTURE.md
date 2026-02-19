# Kubernetes Test Case — Reference Architecture

## Overview

This test case validates an end-to-end ETL pipeline running as Kubernetes Jobs, covering both the **happy path** and **failure path** (intentional schema error injection). It runs in two modes:

| Mode | Cluster | S3 | Observability | CI |
|---|---|---|---|---|
| Local (`test_local.py`) | kind (local) | Real AWS S3 | — | Yes (GitHub Actions) |
| Datadog local (`test_datadog.py`) | kind (local) | Real AWS S3 | Datadog via Helm | Manual |
| EKS (`test_eks.py`, `trigger_alert.py`) | AWS EKS | Real AWS S3 | Datadog via Helm + Lambda trigger | Manual |

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  TEST RUNNER (CI / local machine)                                            │
│                                                                              │
│  test_local.py / test_eks.py / trigger_alert.py                              │
│  infrastructure_sdk/local.py  ·  infrastructure_sdk/eks.py                  │
└────────────────────────┬─────────────────────────────────────────────────────┘
                         │ orchestrates
                         ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  KUBERNETES CLUSTER  (kind locally  /  AWS EKS in cloud)                     │
│  Namespace: tracer-test                                                      │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │  Job: etl-extract   (PIPELINE_STAGE=extract)                            │ │
│  │  Image: tracer-k8s-test:latest                                          │ │
│  │  → reads  s3://landing-bucket/<run-id>/input.json                       │ │
│  │  → writes s3://landing-bucket/<run-id>/extracted.json                   │ │
│  └──────────────────────────────┬──────────────────────────────────────────┘ │
│                                 │ sequential (test runner waits for job)     │
│  ┌──────────────────────────────▼──────────────────────────────────────────┐ │
│  │  Job: etl-transform  (PIPELINE_STAGE=transform)                         │ │
│  │  Happy path  → validates schema, converts amount to cents               │ │
│  │  Error path  → REQUIRED_FIELDS contains unknown field → DomainError     │ │
│  │  → reads  s3://landing-bucket/<run-id>/extracted.json                   │ │
│  │  → writes s3://landing-bucket/<run-id>/transformed.json  (happy only)   │ │
│  └──────────────────────────────┬──────────────────────────────────────────┘ │
│                                 │ (happy path only)                          │
│  ┌──────────────────────────────▼──────────────────────────────────────────┐ │
│  │  Job: etl-load  (PIPELINE_STAGE=load)                                   │ │
│  │  → reads  s3://landing-bucket/<run-id>/transformed.json                 │ │
│  │  → writes s3://processed-bucket/<run-id>/output.json                    │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘

                         │ real read/write
                         ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  AWS S3                                                                      │
│  ┌───────────────────────────────┐   ┌────────────────────────────────────┐  │
│  │  tracer-k8s-landing-*         │   │  tracer-k8s-processed-*            │  │
│  │  <run-id>/input.json          │   │  <run-id>/output.json              │  │
│  │  <run-id>/extracted.json      │   └────────────────────────────────────┘  │
│  │  <run-id>/transformed.json    │                                           │
│  └───────────────────────────────┘                                           │
└──────────────────────────────────────────────────────────────────────────────┘

── EKS / Datadog paths only ────────────────────────────────────────────────────

┌──────────────────────────────────────────────────────────────────────────────┐
│  AWS Lambda  (trigger_lambda/handler.py)                                     │
│  POST /trigger               → happy path (valid data)                       │
│  POST /trigger?inject_error  → error path (bad data, forces DomainError)     │
│  Fronted by API Gateway                                                      │
└───────────────────────────┬──────────────────────────────────────────────────┘
                            │ submits kubectl jobs to EKS
                            ▼ (same K8s jobs as above)

┌──────────────────────────────────────────────────────────────────────────────┐
│  Datadog Agent  (Helm chart: datadog/datadog)                                │
│  • collects container logs from all pods                                     │
│  • collects kube-state-metrics (job.failed counter)                          │
│  Monitors:                                                                   │
│  ├── Metric alert: kubernetes_state.job.failed > 0  → @slack-devs-alerts     │
│  └── Log alert:    "PIPELINE_ERROR" in logs          → @slack-devs-alerts     │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Descriptions

### Pipeline Code (`pipeline_code/`)

A self-contained Python ETL application packaged as a single Docker image. The active stage is selected at runtime via the `PIPELINE_STAGE` environment variable.

| Module | Role |
|---|---|
| `stages/__init__.py` | Dispatcher — routes to extract / transform / load |
| `stages/extract.py` | Reads raw JSON from S3 landing bucket, writes to staging key |
| `stages/transform.py` | Validates schema against `REQUIRED_FIELDS`, converts `amount` to cents |
| `stages/load.py` | Reads staged data, writes final record to processed bucket |
| `domain.py` | Validation + transformation business logic |
| `schemas.py` | `InputRecord` / `ProcessedRecord` dataclasses |
| `errors.py` | `PipelineError` → `DomainError` / `SystemError` hierarchy |
| `config.py` | Reads all config from environment variables |
| `adapters/s3.py` | S3 read/write with `correlation_id` + `processed_at` metadata |

The image is built from `pipeline_code/Dockerfile` (Python 3.11-slim) and tagged `tracer-k8s-test:latest`.

### Kubernetes Manifests (`k8s_manifests/`)

Plain YAML templates with `{{PLACEHOLDER}}` variables replaced at test runtime by `infrastructure_sdk/local.py`. One manifest per pipeline stage plus an error variant for transform.

| Manifest | Stage | Notes |
|---|---|---|
| `job-extract.yaml` | extract | Reads `LANDING_BUCKET` + `S3_KEY` |
| `job-transform.yaml` | transform (success) | `backoffLimit: 0` — fails fast |
| `job-transform-error.yaml` | transform (failure) | Same spec, different labels |
| `job-load.yaml` | load | Also writes `PROCESSED_BUCKET` |
| `namespace.yaml` | — | `tracer-test` namespace |

### Helm Chart (`helm/`)

A thin Helm wrapper around the same ETL job for EKS / Datadog deployments. Exposes `job.injectError` to switch between happy and error paths without maintaining two separate manifests.

### Infrastructure SDK (`infrastructure_sdk/`)

| File | Responsibility |
|---|---|
| `local.py` | kind cluster lifecycle, Docker build/load, `kubectl apply`, job polling, Datadog Helm install, monitor CRUD |
| `eks.py` | EKS cluster + node group creation/teardown, ECR push, IAM roles, API Gateway + Lambda deployment |

### Test Entry Points

| File | When to run |
|---|---|
| `test_local.py` | CI (GitHub Actions) + local dev. kind cluster, real S3. |
| `test_datadog.py` | Manual. kind + Datadog Helm. Requires `DD_API_KEY` / `DD_APP_KEY`. |
| `test_eks.py` | Manual. Full EKS deployment + teardown. Requires full AWS credentials + Datadog keys. |
| `trigger_alert.py` | Manual. Fires alerts against an already-running EKS cluster via Lambda. |

---

## Data Flow (Happy Path)

```
Test Runner
    │
    ├─ uploads input.json to s3://landing/<run-id>/input.json
    │
    ├─ kubectl apply job-extract
    │       Job reads input.json
    │       Writes extracted.json  →  s3://landing/<run-id>/extracted.json
    │
    ├─ kubectl apply job-transform
    │       Job reads extracted.json
    │       Validates schema, converts amount*100
    │       Writes transformed.json  →  s3://landing/<run-id>/transformed.json
    │
    ├─ kubectl apply job-load
    │       Job reads transformed.json
    │       Writes output.json  →  s3://processed/<run-id>/output.json
    │
    └─ asserts output.json exists + content matches expected record
```

## Data Flow (Error / Failure Path)

```
Test Runner
    │
    ├─ uploads malformed or schema-breaking input.json
    │
    ├─ kubectl apply job-extract  (succeeds — no validation here)
    │
    ├─ kubectl apply job-transform-error
    │       Job reads extracted.json
    │       Schema validation fails  →  DomainError("missing required field: X")
    │       Job exits non-zero  →  K8s Job status: Failed
    │       Container logs: "PIPELINE_ERROR ..."
    │
    └─ asserts:
           job status == Failed
           (Datadog path) log alert fires within timeout
           (Datadog path) metric alert fires within timeout
```

---

## Environment Variables

| Variable | Used by | Description |
|---|---|---|
| `PIPELINE_STAGE` | All stages | Selects which stage to run (`extract`/`transform`/`load`) |
| `LANDING_BUCKET` | extract, transform | S3 bucket for raw + staging data |
| `PROCESSED_BUCKET` | load | S3 bucket for final output |
| `S3_KEY` | extract | S3 key of the input file |
| `PIPELINE_RUN_ID` | All stages | Unique ID — used as S3 key prefix |
| `REQUIRED_FIELDS` | transform | Comma-separated list of required fields (error injection: add unknown field) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | All stages | AWS credentials (injected via Job env or IAM role on EKS) |
| `DD_API_KEY` / `DD_APP_KEY` | Datadog paths | Datadog credentials |
| `INJECT_ERROR` | Helm chart path | Set to `"true"` to trigger the error path via Helm |
