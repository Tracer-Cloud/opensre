# Agentic AI for Data Pipeline Incident Resolution

> **Data Engineering Meetup Demo** - Demonstrating how AI agents can autonomously investigate production incidents across your entire data stack.

## 🎯 What This Demo Shows

An AI agent that:
1. **Receives a Grafana alert** about a warehouse freshness SLA breach
2. **Investigates across systems** - S3, Nextflow, warehouse
3. **Tests hypotheses in parallel** - structured, evidence-based reasoning
4. **Produces actionable RCA** - root cause + evidence + fix recommendation

## 🏗️ Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Grafana   │────▶│  AI Agent    │────▶│   Slack     │
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

## 🚀 Quick Start

```bash
# 1. Install dependencies
make install

# 2. Set up environment (add your OpenAI API key)
cp .env.example .env
# Edit .env and add OPENAI_API_KEY

# 3. Run the demo
make demo

# 4. Run tests
make test
```

## 📁 Project Structure

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

## 🎪 Demo Scenario

**The incident**: `events_fact` table freshness SLA breached at 02:13

**What the agent discovers**:
1. ✅ Raw input file exists in S3
2. ✅ Nextflow transformation completed successfully
3. ❌ Nextflow finalize step failed
4. ❌ `_SUCCESS` marker missing
5. ⏳ Service B loader waiting for `_SUCCESS`
6. ⏳ Warehouse table not updated

**Root cause**: Nextflow finalize step did not write the `_SUCCESS` marker, blocking downstream ingestion.

## 📚 Key Code Examples

### 1. Investigation Loop (`src/agent/investigation.py`)
The LangChain agent loop that takes alerts, proposes hypotheses, calls tools, and updates state.

### 2. Hypothesis Model (`src/models/hypothesis.py`)
Pydantic schema for structured hypothesis tracking with evidence requirements.

### 3. Alert Ingestion (`src/models/alert.py`)
Normalizes Grafana alert payloads into clean internal incident objects.

### 4. Context Connectors (`src/tools/`)
Functions that fetch context from S3, Nextflow, and the warehouse.

### 5. Evidence-Backed Report (`src/agent/report_generator.py`)
Assembles root cause, evidence, and recommended fix into actionable output.

## 🔧 Requirements

- Python 3.11+
- OpenAI API key (for LLM reasoning)

## 📖 Related Resources

- [AI Agents for Prod: Full Stack Analysis (Resolve AI)](https://www.youtube.com/watch?v=ApR-unlYQqk)
- Tracer Cloud - [tracercloud.io](https://tracercloud.io)

---

**Built for the Data Engineering Meetup 2026** | Tracer Cloud
