# Data Pipeline Incident Resolution

Data Engineering Meetup Demo - Automated investigation and root cause analysis for production data pipeline incidents using Tracer.

## Overview

This system demonstrates automated incident investigation across a data stack:

1. Receives Grafana alerts for warehouse freshness SLA breaches
2. Investigates pipeline runs using Tracer API
3. Analyzes task status and failure reasons
4. Produces actionable root cause analysis with evidence and fix recommendations

## Architecture

```
+---------------+     +----------------+     +---------------+
|   Grafana     |---->|    Agent       |---->|    Slack      |
|   Alert       |     |  (LangGraph)   |     |    Report     |
+---------------+     +----------------+     +---------------+
                             |
                      +------+------+
                      v             v
                +----------+  +-----------+
                | S3 Mock  |  |  Tracer   |
                |          |  |  Web App  |
                +----------+  +-----------+
```

## Quick Start

### 1. Install dependencies

```bash
make install
```

### 2. Set up environment

Add these to your `.env` file:

```bash
# Anthropic API key for LLM calls (required)
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# Tracer Staging API Configuration
TRACER_API_URL=https://staging.tracer.cloud
TRACER_ORG_ID=org_33W1pou1nUzYoYPZj3OCQ3jslB2
JWT_TOKEN=your_jwt_token_here

# Demo IDs (optional - defaults to demo run)
# trace_id is used for tools/files endpoints
# run_id is used for runs/logs/metrics endpoints
TRACER_TRACE_ID=efb797c9-0226-4932-8eb0-704f03d1752f
TRACER_RUN_ID=b81f28ff-d322-4b0a-a48e-d96f9f26fa82
```

### 3. Get JWT Token

To use real pipeline data from Tracer staging:

1. Log into https://staging.tracer.cloud
2. Open browser DevTools -> Application -> Cookies
3. Copy the `__session` cookie value
4. Set it as `JWT_TOKEN` in your `.env` file

### Demo Pipeline

The default demo uses:
- **Pipeline**: `aws_batch_tests`
- **Run**: `velvet-bear-910`
- **Instance**: g6e.24xlarge with 4x NVIDIA L40S GPUs
- **Tasks**: 6 Parabricks tools for RNA-SEQ processing

### 4. Run the demo

```bash
make demo
```

## Project Structure

```
src/
  agent/
    domain/          # State, prompts, tools
    infrastructure/  # Clients (S3, Tracer) and LLM
    presentation/    # UI rendering and report formatting
    graph.py         # LangGraph state machine
    nodes.py         # Node functions
  tracer/
    client.py        # Tracer HTTP API client
  mocks/
    s3.py            # Mock S3 client
    nextflow.py      # Mock Nextflow (fallback)
  models/
    alert.py         # Alert normalization
    hypothesis.py    # Hypothesis schema
    report.py        # Report models
  main.py            # Entry point
fixtures/
  grafana_alert.json # Sample alert
  mock_data.py       # Mock data
output/              # Generated reports
```

## How It Works

1. **Alert Ingestion**: Receives a Grafana alert and normalizes it
2. **S3 Check**: Checks for output files and _SUCCESS marker
3. **Tracer Run Check**: Queries Tracer API for pipeline run status
4. **Task Check**: Gets task/tool details from Tracer
5. **AWS Batch Check**: Gets AWS Batch job status and failure reasons
6. **Root Cause Analysis**: LLM synthesizes evidence into a root cause
7. **Report Generation**: Creates Slack message and problem.md

## Key Components

### LangGraph State Machine (`src/agent/graph.py`)

Orchestrates the investigation flow:
```
START -> check_s3 -> check_tracer (run + tasks + batch jobs) -> determine_root_cause -> output -> END
```

### Tracer Client (`src/tracer/client.py`)

HTTP client for Tracer staging API with JWT authentication:
- `get_run_details(run_id)` - Get pipeline run details (`/api/runs/{run_id}`)
- `get_tools(trace_id)` - Get tasks/tools (`/api/tools/{trace_id}`)
- `get_batch_jobs(trace_id)` - Get AWS Batch jobs (`/api/aws/batch/jobs/completed`)
- `get_files(trace_id)` - Get files created (`/api/files?traceId=...`)
- `get_host_metrics(run_id)` - Get CPU/RAM/disk metrics (`/api/runs/{run_id}/host-metrics`)
- `get_logs(run_id)` - Get OpenSearch logs (`/api/opensearch/logs?runId=...`)

### Hypothesis Model (`src/models/hypothesis.py`)

Structured hypothesis tracking with evidence requirements.

## Requirements

- Python 3.11+
- Anthropic API key

## Related Resources

- [Tracer Documentation](https://www.tracer.cloud/docs)
- [AI Agents for Prod: Full Stack Analysis](https://www.youtube.com/watch?v=ApR-unlYQqk)

## LangSmith tracing

LangSmith is enabled for the agentic pipeline.
Traces include:
- alert ingestion
- hypothesis generation
- tracer / s3 checks
- root cause synthesis

Controlled via env vars:
- LANGSMITH_TRACING
- LANGSMITH_API_KEY
- LANGSMITH_PROJECT

---

Built for the Data Engineering Meetup 2026 | Tracer Cloud
