# Data Pipeline Incident Resolution

Data Engineering Meetup Demo - Automated investigation and root cause analysis for production data pipeline incidents.

## Overview

This system demonstrates automated incident investigation across a data stack:

1. Receives Grafana alerts for warehouse freshness SLA breaches
2. Investigates across multiple systems (S3, Nextflow, warehouse)
3. Tests hypotheses using structured, evidence-based reasoning
4. Produces actionable root cause analysis with evidence and fix recommendations

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Grafana   │────▶│  Agent       │────▶│   Slack     │
│   Alert     │     │  (LangChain) │     │   Report    │
└─────────────┘     └──────────────┘     └─────────────┘
                           │
                    ┌──────┴──────┐
                    ▼             ▼
              ┌──────────┐  ┌──────────┐
              │   S3     │  │ Nextflow │
              │  (mock)  │  │  (mock)  │
              └──────────┘  └──────────┘
```

## Quick Start

```bash
# Install dependencies
make install

# Set up environment (add your OpenAI API key)
cp .env.example .env
# Edit .env and add OPENAI_API_KEY

# Run the demo
make demo

# Run tests
make test
```

## Project Structure

```
├── src/
│   ├── models/           # Pydantic schemas
│   │   ├── alert.py      # Alert normalization
│   │   ├── hypothesis.py # Hypothesis model
│   │   └── report.py     # RCA report model
│   ├── mocks/            # Mock services
│   │   ├── s3.py         # Mock S3 client
│   │   ├── nextflow.py   # Mock Nextflow API
│   │   └── warehouse.py  # Mock warehouse API
│   ├── tools/            # LangChain tools
│   │   ├── s3_tools.py
│   │   ├── nextflow_tools.py
│   │   └── warehouse_tools.py
│   ├── agent/            # Agent core
│   │   ├── investigation.py  # Investigation loop
│   │   └── report_generator.py
│   └── main.py           # Demo entry point
├── tests/
├── fixtures/             # Sample alert payloads
├── Makefile
├── requirements.txt
└── README.md
```

## Demo Scenario

**Incident**: `events_fact` table freshness SLA breached at 02:13

**Investigation findings**:

1. Raw input file exists in S3
2. Nextflow transformation completed successfully
3. Nextflow finalize step failed
4. `_SUCCESS` marker missing
5. Service B loader waiting for `_SUCCESS`
6. Warehouse table not updated

**Root cause**: Nextflow finalize step did not write the `_SUCCESS` marker, blocking downstream ingestion.

## Key Components

### Investigation Loop (`src/agent/investigation.py`)

LangChain agent loop that processes alerts, proposes hypotheses, calls tools, and updates state.

### Hypothesis Model (`src/models/hypothesis.py`)

Pydantic schema for structured hypothesis tracking with evidence requirements.

### Alert Ingestion (`src/models/alert.py`)

Normalizes Grafana alert payloads into internal incident objects.

### Context Connectors (`src/tools/`)

Functions that fetch context from S3, Nextflow, and the warehouse.

### Report Generator (`src/agent/report_generator.py`)

Assembles root cause, evidence, and recommended fix into actionable output.

## Requirements

- Python 3.11+
- OpenAI API key

## Related Resources

- [AI Agents for Prod: Full Stack Analysis (Resolve AI)](https://www.youtube.com/watch?v=ApR-unlYQqk)
- Tracer Cloud - [tracercloud.io](https://tracercloud.io)

---

Built for the Data Engineering Meetup 2026 | Tracer Cloud
