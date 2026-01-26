# How To Use This Project
- `make install`
- `make demo` to test the project. 

# Data Pipeline Incident Resolution

Data Engineering Meetup Demo - Automated investigation and root cause analysis for production data pipeline incidents using Tracer.

## Overview

This system demonstrates automated incident investigation across a data stack:

1. Receives Grafana alerts for bugs
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

This uses your system `python3` and does not create a virtual environment. On Homebrew-managed Python, this uses `--user --break-system-packages` to satisfy PEP 668.

### 2. Set up environment

Add these to your `.env` file:

```bash
# Anthropic API key for LLM calls (required)
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# Tracer Staging API Configuration
TRACER_API_URL=https://staging.tracer.cloud
TRACER_ORG_ID=org_33W1pou1nUzYoYPZj3OCQ3jslB2
JWT_TOKEN=your_jwt_token_here

# Tracer Web App API (Next.js)
# Use localhost when running the web app locally, or staging if not.
TRACER_WEB_APP_URL=https://staging.tracer.cloud

# Demo IDs (optional - defaults to demo run)
# trace_id is used for tools/files endpoints
# run_id is used for runs/logs/metrics endpoints
TRACER_TRACE_ID=efb797c9-0226-4932-8eb0-704f03d1752f
TRACER_RUN_ID=b81f28ff-d322-4b0a-a48e-d96f9f26fa82
```

