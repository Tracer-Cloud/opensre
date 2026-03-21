# Local Setup Guide

This guide covers two local paths:

- A bundled local RCA demo with the least setup friction
- The full local development flow with your Tracer account

## Prerequisites

- Python 3.11+
- `make`

## 1. Fastest path: bundled local Grafana RCA demo

If you want to see a minimal Grafana-style RCA report locally as quickly as possible, start here.

1. Install dependencies:

   ```bash
   make install
   ```

2. Copy the example env file:

   ```bash
   cp .env.example .env
   ```

3. Add one LLM key to `.env`:

   ```bash
   ANTHROPIC_API_KEY=your-anthropic-api-key
   ```

   Or, if you prefer OpenAI:

   ```bash
   LLM_PROVIDER=openai
   OPENAI_API_KEY=your-openai-api-key
   ```

4. Run the bundled Grafana RCA example:

   ```bash
   make local-grafana-demo
   ```

This path uses bundled Grafana-style alert and evidence data. It does not require a Tracer account or real Grafana, Slack, Datadog, or AWS credentials.

If you want the generic bundled RCA example instead, run:

```bash
make local-rca-demo
```

If you want the same experience against a real local Grafana stack, see [docs/local-grafana-live.md](docs/local-grafana-live.md).

## 2. Full local development setup

Use this path when you want to run the agent locally with your Tracer account and your own integrations.

### Install dependencies

```bash
make install
```

### Configure env variables

1. Copy the example env file:

   ```bash
   cp .env.example .env
   ```

2. Go to `https://app.tracer.cloud`, sign in, and create or copy your Tracer API token from settings.
3. In your local `.env`, set the tracer JWT token and other env variables(for example):

   ```bash
   JWT_TOKEN=your-tracer-token-from-app.tracer.cloud
   ANTHROPIC_API_KEY=your-anthropic-api-key
   ```

You can use `.env.example` as a reference for any other optional integrations you want to enable.

### Dry run the CLI (no external calls)

Use dry run to validate an alert payload and preview investigation flow without executing LLM/tool/API actions:

```bash
python -m app.main --input ./app/alert.json --dry-run
```

This mode:
- Parses and validates the input payload
- Prints planned investigation steps
- Exits without running the investigation


### Run the LangGraph dev UI

Start the LangGraph dev server:

```bash
make dev
```

Then open `http://localhost:2024` in your browser. From there you can send alerts to the agent and inspect the graph step by step while developing.
